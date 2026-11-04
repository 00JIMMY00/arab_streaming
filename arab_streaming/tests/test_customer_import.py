from io import BytesIO
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.file_manager import save_file
from openpyxl import load_workbook

from arab_streaming.customer_import import HEADERS, check_access, prepare, process, read_rows, job_status, run_job, report_progress, _start_process


class TestCustomerImport(FrappeTestCase):
	def setUp(self):
		self.user = frappe.session.user
		self.role = frappe.conf.get("arab_streaming_site_role")
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.conf.arab_streaming_enabled = False
		self.customer_name = "IMPORT-TEST-" + frappe.generate_hash(length=10)
		self.defaults = dict(customer_group=None, territory=None, country="Egypt")

	def tearDown(self):
		frappe.db.rollback()
		frappe.conf.arab_streaming_site_role = self.role
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.set_user(self.user)

	def row(self):
		return [self.customer_name, "01012345678", "+201112345678", "01212345678", "", "Individual", "Home", "04", "05", "Test street", "Cairo", "", "Office", "", "", "Office street", "Cairo", ""]

	def workbook(self, rows):
		book = load_workbook(frappe.get_app_path("arab_streaming", "public", "templates", "customer-import-template.xlsx"))
		for row in rows:
			book.active.append(row)
		content = BytesIO()
		book.save(content)
		book.close()
		return content.getvalue()

	def test_original_headers_and_text_phones(self):
		rows = read_rows(self.workbook([self.row()]))
		self.assertEqual(len(HEADERS), 18)
		self.assertEqual(rows[0][0], 2)
		self.assertEqual(rows[0][1][1:4], self.row()[1:4])
		self.assertEqual(rows[0][1][7], "04")

	def test_long_address_title_fails_preview_before_any_inserts(self):
		row = self.row()
		row[6] = "ع" * 141
		results, _ = prepare([(1467, row)], self.defaults)
		self.assertEqual(results[0]["status"], "Error")
		self.assertIn("Address Title", results[0]["message"])
		self.assertIn("140", results[0]["message"])
		self.assertEqual(results[0]["row"], 1467)
		row[6] = "ع" * 140
		self.assertEqual(prepare([(2, row)], self.defaults)[0][0]["status"], "Ready")

	def test_job_progress_and_cached_result_survive_rq_expiry(self):
		job_id = "customer-import-" + frappe.generate_hash(length=12)
		frappe.cache.set_value(job_id, frappe.session.user, expires_in_sec=60)
		frappe.local.customer_import_job_id = job_id
		try:
			report_progress("importing", 30, 100)
			with patch("frappe.utils.background_jobs.get_job") as get_job:
				get_job.return_value.get_status.return_value = "started"
				self.assertEqual(job_status(job_id)["progress"]["completed"], 30)
				get_job.return_value = None
				self.assertEqual(job_status(job_id)["status"], "finished")
				self.assertIn("missing", job_status(job_id)["result"]["error"])
				frappe.cache.set_value(job_id + ":result", {"imported": 7}, expires_in_sec=60)
				self.assertEqual(job_status(job_id)["result"]["imported"], 7)
			frappe.cache.set_value(job_id, "another-user", expires_in_sec=60)
			with self.assertRaises(frappe.PermissionError):
				job_status(job_id)
		finally:
			frappe.local.customer_import_job_id = None
			for suffix in ("", ":progress", ":result"):
				frappe.cache.delete_value(job_id + suffix)

	def test_worker_caches_failure_and_rolls_back(self):
		job_id = "customer-import-" + frappe.generate_hash(length=12)
		try:
			with patch("rq.get_current_job") as current, patch("arab_streaming.customer_import.process", side_effect=ValueError("controller rejected row")):
				current.return_value.id = "streaming-master.test::" + job_id
				result = run_job()
			self.assertEqual(result, {"error": "controller rejected row"})
			self.assertEqual(frappe.cache.get_value(job_id + ":result"), result)
			self.assertIsNone(frappe.local.customer_import_job_id)
		finally:
			frappe.cache.delete_value(job_id + ":result")

	def test_duplicate_start_reconnects_and_never_enqueues_another_import(self):
		active = dict(job_id="customer-import-existing", owner="Administrator", file_url="/private/files/existing.xlsx", apply=1, defaults=self.defaults)
		with patch("arab_streaming.customer_import.active_job", return_value=active), patch("frappe.enqueue") as enqueue:
			result = _start_process("/private/files/another.xlsx", None, None, None, 0, None)
			self.assertTrue(result["resumed"])
			self.assertEqual(result["job_id"], active["job_id"])
			self.assertEqual(result["apply"], 1)
			enqueue.assert_not_called()
			active["owner"] = "another-user"
			with self.assertRaisesRegex(frappe.ValidationError, "Another customer import is running"):
				_start_process("file.xlsx", None, None, None, 1, None)

	def test_running_job_recovers_ownership_after_cache_clear(self):
		job_id = "customer-import-" + frappe.generate_hash(length=12)
		try:
			with patch("frappe.utils.background_jobs.get_job") as get_job:
				get_job.return_value.kwargs = dict(site=frappe.local.site, user="Administrator", method="arab_streaming.customer_import.run_job")
				get_job.return_value.get_status.return_value = "started"
				self.assertEqual(job_status(job_id)["status"], "started")
				self.assertEqual(frappe.cache.get_value(job_id, expires=True), "Administrator")
		finally:
			frappe.cache.delete_value(job_id)

	def test_import_above_former_500_row_limit(self):
		rows = [[f"{self.customer_name}-{index}", "", "", "", "", "Individual"] + [""] * 12 for index in range(501)]
		file = save_file("customer-import-bulk-test.xlsx", self.workbook(rows), None, None, is_private=1)
		with patch.object(frappe.db, "commit"):
			preview = process(file.file_url, **self.defaults)
			self.assertEqual(len(preview["rows"]), 501)
			result = process(file.file_url, **self.defaults, apply=1, digest=preview["digest"])
			self.assertEqual(result["imported"], 501)
			self.assertTrue(frappe.db.exists("Customer", result["rows"][-1]["name"]))

	def test_optional_fields_and_conditional_address_requirements(self):
		row = [self.customer_name, "", "", "", "", "Company"] + [""] * 12
		results, plans = prepare([(2, row)], self.defaults)
		self.assertEqual(results[0]["status"], "Ready")
		self.assertEqual(plans[0][1:], ([], []))
		row[6] = "Home"
		results, _ = prepare([(2, row)], self.defaults)
		self.assertEqual(results[0]["status"], "Error")
		self.assertIn("Address Line 1 and City", results[0]["message"])
		row[0], row[5] = "", "Unknown"
		results, _ = prepare([(2, row)], self.defaults)
		self.assertIn("Customer Name is required", results[0]["message"])
		self.assertIn("Customer Type must be", results[0]["message"])

	def test_duplicates_and_invalid_group(self):
		row = self.row()
		results, _ = prepare([(2, row), (3, row)], self.defaults)
		self.assertEqual(results[1]["status"], "Error")
		row[4] = "NONEXISTENT-" + frappe.generate_hash(length=10)
		results, _ = prepare([(2, row)], self.defaults)
		self.assertEqual(results[0]["status"], "Error")

	def test_formula_and_empty_workbook_rejected(self):
		row = self.row()
		row[1] = "=1+1"
		with self.assertRaises(frappe.ValidationError):
			read_rows(self.workbook([row]))
		with self.assertRaises(frappe.ValidationError):
			read_rows(self.workbook([]))

	def test_master_and_user_access(self):
		frappe.conf.arab_streaming_site_role = "Slave"
		with self.assertRaises(frappe.PermissionError):
			check_access()
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			check_access()

	def test_import_creates_links_primary_records_and_skips_reupload(self):
		file = save_file("customer-import-test.xlsx", self.workbook([self.row()]), None, None, is_private=1)
		with patch.object(frappe.db, "commit"):
			preview = process(file.file_url, **self.defaults)
			self.assertEqual(preview["rows"][0]["status"], "Ready")
			self.assertFalse(frappe.db.exists("Customer", {"customer_name": self.customer_name}))
			result = process(file.file_url, **self.defaults, apply=1, digest=preview["digest"])
			self.assertEqual(result["imported"], 1)
			customer = frappe.get_doc("Customer", result["rows"][0]["name"])
			links = frappe.get_all("Dynamic Link", filters={"link_doctype": "Customer", "link_name": customer.name}, fields=["parent", "parenttype"])
			self.assertEqual(sum(r.parenttype == "Contact" for r in links), 3)
			self.assertEqual(sum(r.parenttype == "Address" for r in links), 2)
			self.assertTrue(customer.customer_primary_contact)
			self.assertEqual(frappe.db.get_value("Contact", customer.customer_primary_contact, "mobile_no"), "01012345678")
			self.assertEqual(frappe.db.get_value("Address", customer.customer_primary_address, "address_line2"), "Apartment 04, Building 05")
			again = process(file.file_url, **self.defaults, apply=1, digest=preview["digest"])
			self.assertEqual(again["imported"], 0)
			self.assertEqual(again["rows"][0]["status"], "Skipped")
			with self.assertRaises(frappe.ValidationError):
				process(file.file_url, **dict(self.defaults, country="Canada"), apply=1, digest=preview["digest"])

	def test_controller_failure_rolls_back_whole_batch(self):
		row2 = self.row()
		row2[0] += "-SECOND"
		file = save_file("customer-import-rollback.xlsx", self.workbook([self.row(), row2]), None, None, is_private=1)
		preview = process(file.file_url, **self.defaults)
		from erpnext.selling.doctype.customer.customer import Customer
		original = Customer.validate

		def validate(doc):
			if doc.customer_name == row2[0]:
				frappe.throw("Test controller rejection")
			return original(doc)

		with patch.object(Customer, "validate", validate), self.assertRaises(frappe.ValidationError):
			process(file.file_url, **self.defaults, apply=1, digest=preview["digest"])
		self.assertFalse(frappe.db.exists("Customer", {"customer_name": self.customer_name}))
		self.assertFalse(frappe.db.exists("Customer", {"customer_name": row2[0]}))
