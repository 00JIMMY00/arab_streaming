"""Explicit catalog capability boundary; ordinary permissions and validation apply."""

import frappe
from frappe.model import display_fieldtypes
from frappe.utils import escape_html

# A denylist for framework machinery and generated results is not a business
# allowlist. New/custom business DocTypes are discovered from metadata.
BLOCKED_TYPES = frozenset(
	{
		"DocType",
		"DocField",
		"DocPerm",
		"DocType Link",
		"Custom Field",
		"Property Setter",
		"Role",
		"Has Role",
		"User Permission",
		"Session Default",
		"Sessions",
		"User Session Display",
		"DefaultValue",
		"Error Log",
		"Activity Log",
		"Version",
		"Comment",
		"Communication",
		"Notification Log",
		"Scheduled Job Type",
		"Scheduled Job Log",
		"RQ Job",
		"Server Script",
		"Client Script",
		"Webhook",
		"File",
		"GL Entry",
		"Stock Ledger Entry",
		"Payment Ledger Entry",
		"Bin",
		"Repost Item Valuation",
		"Repost Accounting Ledger",
		"Repost Payment Ledger",
		"Prepared Report",
		"Access Log",
		"Document Share Key",
		"OAuth Client",
		"OAuth Bearer Token",
		"Integration Request",
		"Data Import",
		"DocShare",
	}
)


def is_business_doctype(doctype):
	if not isinstance(doctype, str) or doctype in BLOCKED_TYPES or not frappe.db.exists("DocType", doctype):
		return False
	meta = frappe.get_meta(doctype)
	if meta.module == "Arab Streaming" or meta.istable or meta.get("is_virtual"):
		return False
	if (
		meta.module
		in ("Core", "Email", "Desk", "Custom", "Automation", "Integrations", "Printing", "Workflow")
		and not meta.custom
		and doctype != "User"
	):
		return False
	return not meta.issingle


class BusinessTypes:
	def __contains__(self, doctype):
		return is_business_doctype(doctype)

	def __iter__(self):
		return iter(
			[
				name
				for name in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, pluck="name")
				if is_business_doctype(name)
			]
		)


SAFE_TYPES = BusinessTypes()  # Backward-compatible import; membership is dynamic.

# Item creation helpers generate independent prices/stock transactions. These
# and calculated/local operational values are not part of catalog replication.
LOCAL_FIELDS = {
	"User": frozenset(
		{
			"new_password",
			"api_key",
			"api_secret",
			"reset_password_key",
			"last_reset_password_key_generated_on",
			"last_password_reset_date",
			"last_active",
			"last_login",
			"last_ip",
			"last_known_versions",
			"social_logins",
			"user_emails",
			"send_welcome_email",
			"logout_all_sessions",
		}
	),
	"Item": frozenset(
		{
			"opening_stock",
			"standard_rate",
			"valuation_rate",
			"last_purchase_rate",
			"total_projected_qty",
			"default_bom",
			"default_item_manufacturer",
			"default_manufacturer_part_no",
		}
	),
}


def require_supported(doctype):
	if not is_business_doctype(doctype):
		frappe.throw(
			"Select an installed business parent DocType. Framework/authentication records and generated ledger rows are not independent synchronization roots."
		)
	return frappe.get_meta(doctype)


def transferable_fields(meta):
	from arab_streaming.protocol.codec import EXCLUDE

	return [
		field
		for field in meta.fields
		if field.fieldname not in EXCLUDE | LOCAL_FIELDS.get(meta.name, frozenset())
		and field.fieldtype not in (*display_fieldtypes, "Password")
		and not field.get("is_virtual")
	]


def validate_document(document, expected=None, depth=0):
	if not isinstance(document, dict) or depth > 10:
		frappe.throw("Invalid catalog document structure")
	doctype = document.get("doctype")
	if expected:
		if doctype != expected:
			frappe.throw("Child row DocType does not match its catalog table")
		meta = frappe.get_meta(expected)
	else:
		meta = require_supported(doctype)
	if (
		not isinstance(document.get("name"), str)
		or not document["name"]
		or document.get("docstatus", 0) not in (0, 1, 2)
	):
		frappe.throw("This adapter accepts named business records with a valid document status")
	fields = transferable_fields(meta)
	if not document.keys() <= {"doctype", "name", "docstatus"} | {field.fieldname for field in fields}:
		frappe.throw("Catalog payload contains unsupported or site-local fields")
	for field in fields:
		value = document.get(field.fieldname)
		if field.fieldtype in ("Table", "Table MultiSelect"):
			if not isinstance(value, list):
				frappe.throw("Catalog child tables must contain row lists")
			for child in value:
				validate_document(child, field.options, depth + 1)
				owner = frappe.db.get_value(
					field.options, child["name"], ["parent", "parenttype", "parentfield"], as_dict=True
				)
				if owner and (owner.parent, owner.parenttype, owner.parentfield) != (
					document["name"],
					doctype,
					field.fieldname,
				):
					frappe.throw("Catalog child row identity belongs to another document")

		elif field.fieldtype in ("Attach", "Attach Image") and value:
			if value.startswith(("/files/", "/private/files/")) and not frappe.db.exists(
				"File", {"file_url": value}
			):
				frappe.throw("Attachment must be imported and verified before applying this document")


def linked_records(document, path=None):
	"""Include links inside child tables, not only the parent's direct Links."""
	meta = frappe.get_meta(document["doctype"])
	path = path or document["doctype"]
	for field in transferable_fields(meta):
		value = document.get(field.fieldname)
		location = path + "." + field.fieldname
		if field.fieldtype in ("Table", "Table MultiSelect"):
			for position, child in enumerate(value or [], 1):
				yield from linked_records(child, f"{location}[{position}]")
		elif value and field.fieldtype in ("Link", "Dynamic Link"):
			doctype = field.options if field.fieldtype == "Link" else document.get(field.options)
			if not isinstance(doctype, str) or not isinstance(value, str):
				frappe.throw("Invalid catalog link")
			yield doctype, value, location


def missing_dependency(doctype, name, path):
	frappe.throw(
		"Missing catalog dependency: "
		+ escape_html(f"{doctype} {name} at {path}")
		+ ". Create or map this record on the receiving site, then retry the Capture Event. "
		"Company, Warehouse, accounts and other transaction setup are not created automatically."
	)


def condition_fields(doctype):
	meta = require_supported(doctype)
	return {"name", "docstatus", "creation", "modified", "owner"} | {
		field.fieldname
		for field in meta.fields
		if field.fieldtype
		not in (
			"Password",
			"Table",
			"Table MultiSelect",
			"HTML",
			"Button",
			"Section Break",
			"Column Break",
			"Tab Break",
		)
	}


def validate_config(row):
	require_supported(row.doctype_name)
	if row.get("directly_inserted_in_db") or row.get("ignore_validation"):
		frappe.throw("This adapter requires normal Frappe validation; bypass modes are not enabled")
