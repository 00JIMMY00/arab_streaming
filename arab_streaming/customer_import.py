"""Master-only customer spreadsheet import using ordinary ERPNext documents."""

import json
import uuid
import time
from hashlib import sha256
from io import BytesIO
from zipfile import BadZipFile, ZipFile

import frappe
from frappe.utils import cint, cstr
from openpyxl import load_workbook
from redis.exceptions import LockError

HEADERS = [
	"Customer Name (Mandatory)", "Phone 1", "Phone 2", "Phone 3",
	"Customer Group (Optional)", "Customer Type (Mandatory)",
	"Address 1", "Apartment Number 1", "Building Number 1",
	"Address Line 1 for address 1", "City for address 1", "State for address 1",
	"Address 2", "Apartment Number 2", "Building Number 2",
	"Address Line 1 for address 2", "City for address 2", "State for address 2",
]


def check_access():
	if frappe.conf.get("arab_streaming_site_role") != "Master":
		frappe.throw("Customer Import Tool is available on the master instance only.", frappe.PermissionError)
	frappe.only_for("System Manager")
	for dt in ("Customer", "Address", "Contact"):
		frappe.has_permission(dt, "create", throw=True)
		frappe.has_permission(dt, "read", throw=True)


@frappe.whitelist()
def settings():
	check_access()
	active = active_job()
	return dict(
		customer_group=frappe.db.get_single_value("Selling Settings", "customer_group"),
		territory=frappe.db.get_single_value("Selling Settings", "territory"),
		country=frappe.defaults.get_global_default("country"),
		customer_types=frappe.get_meta("Customer").get_field("customer_type").options.split("\n"),
		current_job=dict(job_id=active["job_id"], file_url=active["file_url"], apply=active["apply"], defaults=active["defaults"], resumed=True) if active and active["owner"] == frappe.session.user else None,
	)


def cell_text(cell):
	if cell.data_type == "f":
		frappe.throw(f"Cell {cell.coordinate}: formulas are not supported. Paste values instead.")
	value = cell.value
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	return cstr(value).strip()


def read_rows(content):
	if len(content) > 5 * 1024 * 1024:
		frappe.throw("Upload an .xlsx file smaller than 5 MB.")
	try:
		with ZipFile(BytesIO(content)) as archive:
			if sum(f.file_size for f in archive.infolist()) > 25 * 1024 * 1024:
				frappe.throw("The uncompressed workbook is too large.")
		book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
	except (BadZipFile, ValueError, KeyError, OSError):
		frappe.throw("This file is not a valid .xlsx workbook.")
	try:
		sheet = book.worksheets[0]
		if sheet.max_column and sheet.max_column > len(HEADERS):
			frappe.throw("Use the 18 columns in the downloaded template without adding columns.")
		values = sheet.iter_rows(max_col=len(HEADERS))
		if [cell_text(c) for c in next(values)] != HEADERS:
			frappe.throw("The column headings do not match. Download the template and keep its header row unchanged.")
		rows = []
		for row_number, cells in enumerate(values, 2):
			row = [cell_text(c) for c in cells]
			if any(row):
				rows.append((row_number, row))
		if not rows:
			frappe.throw("The workbook has no customer rows. Fill in the template and upload it again.")
		return rows
	finally:
		book.close()


def linked_customer(name):
	return [dict(link_doctype="Customer", link_name=name)]


def report_progress(phase, completed, total):
	job_id = getattr(frappe.local, "customer_import_job_id", None)
	if job_id:
		frappe.cache.set_value(job_id + ":progress", dict(
			phase=phase, completed=completed, total=total, updated=time.time(),
		), expires_in_sec=10800)


def prepare(rows, defaults):
	results, plans, seen = [], [], set()
	report_progress("validating", 0, len(rows))
	types = frappe.get_meta("Customer").get_field("customer_type").options.split("\n")
	for number, row in rows:
		name, customer_type = row[0], row[5]
		errors = []
		if not name:
			errors.append("Customer Name is required")
		if customer_type not in types:
			errors.append("Customer Type must be " + ", ".join(types))
		if name.casefold() in seen:
			errors.append("Customer Name is repeated in this workbook")
		seen.add(name.casefold())
		group = row[4] or defaults.get("customer_group")
		territory = defaults.get("territory")
		customer = frappe.new_doc("Customer")
		customer.update(dict(customer_name=name, customer_type=customer_type, customer_group=group or "", territory=territory or ""))
		if group and frappe.db.get_value("Customer Group", group, "is_group"):
			errors.append("Customer Group must be a non-group Customer Group")
		addresses = []
		for index, start in enumerate((6, 12), 1):
			title, apartment, building, line1, city, state = row[start:start + 6]
			if not any(row[start:start + 6]):
				continue
			if not line1 or not city:
				errors.append(f"Address {index}: Address Line 1 and City are required when any address field is filled")
			if not defaults.get("country"):
				errors.append(f"Address {index}: select a Country on this page")
			address = frappe.new_doc("Address")
			address.update(dict(
				address_title=title or f"{name} Address {index}", address_type="Billing" if index == 1 else "Shipping",
				address_line1=line1, city=city, state=state, country=defaults.get("country"),
				address_line2=", ".join(v for v in (f"Apartment {apartment}" if apartment else "", f"Building {building}" if building else "") if v),
				is_primary_address=int(index == 1), is_shipping_address=int(index == 2),
			))
			# Preserve structured fields if this site has installed them, plus a portable address line.
			for field, value in (("apartment_number", apartment), ("building_number", building)):
				for candidate in (field, "custom_" + field):
					if address.meta.has_field(candidate):
						address.set(candidate, value)
			addresses.append(address)
		contacts, phones = [], set()
		for index, phone in enumerate(row[1:4], 1):
			if not phone or phone in phones:
				continue
			phones.add(phone)
			contact = frappe.new_doc("Contact")
			contact.update(dict(first_name=f"{name} Contact {index}", is_primary_contact=int(not contacts)))
			contact.append("phone_nos", dict(phone=phone, is_primary_phone=1, is_primary_mobile_no=1))
			contacts.append(contact)
		for doc in [customer, *addresses, *contacts]:
			doc._action = "save"
			for df in doc.meta.fields:
				if df.reqd and not doc.get(df.fieldname) and df.fieldtype not in ("Table", "Table MultiSelect"):
					errors.append(f"{doc.doctype}: {df.label} is required by this site")
			try:
				doc._validate_links()
				doc._validate_data_fields()
				# Match native insert validation, including child phone fields.
				doc._validate_length()
				for child in doc.get_all_children():
					child._validate_length()
			except frappe.ValidationError as exc:
				errors.append(cstr(exc))
		existing = frappe.get_all("Customer", filters={"customer_name": name}, pluck="name", limit=2) if name else []
		if len(existing) > 1:
			errors.append("Multiple existing customers have this name; resolve the duplicate names first")
		status = "Error" if errors else "Skipped" if existing else "Ready"
		results.append(dict(row=number, customer=name, status=status, message="; ".join(dict.fromkeys(errors)) if errors else "Existing customer; no changes" if existing else f"{len(contacts)} contact(s), {len(addresses)} address(es)"))
		plans.append((customer, contacts, addresses))
		if len(results) % 25 == 0 or len(results) == len(rows):
			report_progress("validating", len(results), len(rows))
	return results, plans


@frappe.whitelist(methods=["POST"])
def process(file_url, customer_group=None, territory=None, country=None, apply=0, digest=None):
	check_access()
	file = frappe.get_doc("File", {"file_url": file_url})
	file.check_permission("read")
	if file.owner != frappe.session.user or not file.is_private:
		frappe.throw("Upload your own private workbook using this page.", frappe.PermissionError)
	if not file.file_name.lower().endswith(".xlsx"):
		frappe.throw("Only .xlsx workbooks are supported.")
	content = file.get_content()
	defaults = dict(customer_group=customer_group, territory=territory, country=country)
	fingerprint = sha256(content + json.dumps(defaults, sort_keys=True).encode()).hexdigest()
	applying = cint(apply)
	if applying and digest != fingerprint:
		frappe.throw("The file or defaults changed. Validate the workbook again before importing.")
	# Serialize this tool's imports; re-read duplicates under the lock. Normal controllers and hooks stay active.
	with frappe.cache.lock("arab_streaming_customer_import", timeout=7200, blocking_timeout=2):
		results, plans = prepare(read_rows(content), defaults)
		if applying:
			if any(r["status"] == "Error" for r in results):
				frappe.throw("Correct all row errors and validate again. No customers were imported.")
			try:
				report_progress("importing", 0, len(results))
				for index, (result, (customer, contacts, addresses)) in enumerate(zip(results, plans, strict=True), 1):
					if result["status"] == "Skipped":
						report_progress("importing", index, len(results))
						continue
					customer.insert()
					for doc in [*contacts, *addresses]:
						doc.set("links", linked_customer(customer.name))
						doc.insert()
					customer.customer_primary_contact = contacts[0].name if contacts else None
					customer.customer_primary_address = addresses[0].name if addresses else None
					customer.save()
					result.update(status="Imported", name=customer.name)
					if index % 10 == 0 or index == len(results):
						report_progress("importing", index, len(results))
				frappe.db.commit()
			except Exception as exc:
				frappe.db.rollback()
				frappe.throw(f"Row {result['row']}: {cstr(exc)}. The entire import was rolled back; no customers were imported.")
	return dict(rows=results, digest=fingerprint, imported=sum(r["status"] == "Imported" for r in results))


@frappe.whitelist(methods=["POST"])
def start_process(file_url, customer_group=None, territory=None, country=None, apply=0, digest=None):
	"""Run large workbooks outside the web request timeout."""
	check_access()
	with frappe.cache.lock("arab_streaming_customer_import_start", timeout=15, blocking_timeout=5):
		return _start_process(file_url, customer_group, territory, country, apply, digest)


def active_job():
	from frappe.utils.background_jobs import get_job, get_queue

	active = frappe.cache.get_value("customer-import-active", expires=True)
	if active:
		job = get_job(active["job_id"])
		if job and job.get_status() in ("queued", "started", "deferred", "scheduled"):
			return active
		if not job and time.time() - active.get("created", 0) < 30:
			return active
	# Discover jobs started before this protection was deployed.
	queue = get_queue("long")
	for job_id in queue.started_job_registry.get_job_ids() + queue.get_job_ids():
		job = get_job(job_id.split("::")[-1])
		if not job or job.kwargs.get("site") != frappe.local.site or job.kwargs.get("method") != "arab_streaming.customer_import.run_job":
			continue
		args = job.kwargs.get("kwargs", {})
		return dict(job_id=job.id.split("::")[-1], owner=job.kwargs.get("user"),
			file_url=args.get("file_url"), apply=cint(args.get("apply")),
			defaults={key: args.get(key) for key in ("customer_group", "territory", "country")})


def _start_process(file_url, customer_group, territory, country, apply, digest):
	active = active_job()
	if active:
		if active["owner"] != frappe.session.user:
			frappe.throw("Another customer import is running. Wait for it to finish before starting a new one.")
		return dict(job_id=active["job_id"], file_url=active["file_url"], apply=active["apply"], defaults=active["defaults"], resumed=True)
	job_id = "customer-import-" + str(uuid.uuid4())
	frappe.cache.set_value(job_id, frappe.session.user, expires_in_sec=10800)
	frappe.cache.set_value(job_id + ":progress", dict(phase="queued", completed=0, total=0, updated=time.time()), expires_in_sec=10800)
	frappe.cache.set_value("customer-import-active", dict(job_id=job_id, owner=frappe.session.user,
		file_url=file_url, apply=cint(apply), created=time.time(),
		defaults=dict(customer_group=customer_group, territory=territory, country=country)), expires_in_sec=10800)
	frappe.enqueue(
		"arab_streaming.customer_import.run_job", queue="long", timeout=7200,
		job_id=job_id, enqueue_after_commit=True,
		file_url=file_url, customer_group=customer_group, territory=territory,
		country=country, apply=apply, digest=digest,
	)
	return dict(job_id=job_id)


def run_job(**kwargs):
	from rq import get_current_job

	job = get_current_job()
	job_id = job.id.split("::")[-1] if job else None
	frappe.local.customer_import_job_id = job_id
	try:
		result = process(**kwargs)
	except LockError:
		frappe.db.rollback()
		result = dict(error="Another customer import is running. Wait for it to finish, then upload again to validate before retrying.")
	except Exception as exc:
		frappe.db.rollback()
		result = dict(error=cstr(exc))
	finally:
		frappe.local.customer_import_job_id = None
	if job_id:
		# RQ normally expires successful results after ten minutes.
		frappe.cache.set_value(job_id + ":result", result, expires_in_sec=10800)
	return result


@frappe.whitelist()
def job_status(job_id):
	check_access()
	from frappe.utils.background_jobs import get_job

	if not cstr(job_id).startswith("customer-import-"):
		frappe.throw("This import job is not available to your user.", frappe.PermissionError)
	owner = frappe.cache.get_value(job_id, expires=True)
	job = None
	if owner is None:
		# A site cache clear must not strand a running import.
		job = get_job(job_id)
		if job and job.kwargs.get("site") == frappe.local.site and job.kwargs.get("method") == "arab_streaming.customer_import.run_job":
			owner = job.kwargs.get("user")
			frappe.cache.set_value(job_id, owner, expires_in_sec=10800)
	if owner != frappe.session.user:
		frappe.throw("This import job is not available to your user.", frappe.PermissionError)
	result = frappe.cache.get_value(job_id + ":result", expires=True)
	if result is not None:
		return dict(status="finished", result=result)
	progress = frappe.cache.get_value(job_id + ":progress", expires=True)
	job = job or get_job(job_id)
	if not job:
		return dict(status="finished", result=dict(error="The background job is missing or its result expired. Upload again to validate existing customers before retrying."))
	status = job.get_status()
	if status == "finished":
		return dict(status=status, result=job.result)
	if status in ("failed", "stopped", "canceled"):
		return dict(status="finished", result=dict(error="The background job stopped. Validate the workbook again to check existing customers before retrying."))
	return dict(status=status, progress=progress)
