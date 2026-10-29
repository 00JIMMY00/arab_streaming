import json
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.streaming.adapters import require_supported
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.policy import authorize_document, direction_allowed, matches_scope


class TestDynamicRules(FrappeTestCase):
	def test_translated_doctype_link_search(self):
		from frappe.desk.search import search_link

		query = "arab_streaming.api.rules.doctype_query"
		for text, expected in [
			("customer", "Customer"),
			("Customer", "Customer"),
			("supplier", "Supplier"),
			("sales invoice", "Sales Invoice"),
			("uom", "UOM"),
		]:
			with self.subTest(text=text):
				results = search_link("DocType", text, query=query, page_length=10)
				self.assertIn(expected, [row["value"] for row in results])
		results = search_link("DocType", "customer", query=query, page_length=1)
		self.assertEqual([row["value"] for row in results], ["Customer"])
		self.assertNotIn("User", [row["value"] for row in search_link("DocType", "user", query=query)])

	def setUp(self):
		self.user = frappe.session.user
		self.role = frappe.conf.get("arab_streaming_site_role")
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.conf.arab_streaming_enabled = False
		frappe.db.savepoint("dynamic_case")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="dynamic_case")
		frappe.conf.arab_streaming_site_role = self.role
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.set_user(self.user)

	def test_sales_invoice_and_other_business_types_are_dynamic(self):
		for dt in [
			"Sales Invoice",
			"Purchase Invoice",
			"Sales Order",
			"Customer",
			"Supplier",
			"Price List",
			"Item Price",
		]:
			self.assertEqual(require_supported(dt).name, dt)
		for dt in ["User", "DocType", "GL Entry", "Stock Ledger Entry", "Bin", "Capture Event"]:
			with self.assertRaises(frappe.ValidationError):
				require_supported(dt)

	def test_branch_and_direction_isolation_for_ten_peers(self):
		config = frappe._dict(doctype_name="Branch", scope="Branch", branch_field="parent_branch")
		# Resolver uses real field metadata; each branch-shaped document has one explicit Branch Link.
		with patch("arab_streaming.streaming.policy._values", side_effect=lambda doc, parts: [doc["branch"]]):
			config.branch_field = "branch"
			for n in range(10):
				branch = f"Branch {n:02d}"
				doc = {"doctype": "Sales Invoice", "name": f"INV-{n:02d}", "branch": branch}
				for target in range(10):
					self.assertEqual(matches_scope(doc, config, f"Branch {target:02d}"), n == target)
				with self.assertRaises(frappe.PermissionError):
					authorize_document(frappe._dict(restaurant_branch="Other"), doc, config=config)
		for local in ["Master", "Slave"]:
			frappe.conf.arab_streaming_site_role = local
			self.assertTrue(direction_allowed("Both"))
			self.assertTrue(direction_allowed("Both", True))
			self.assertNotEqual(
				direction_allowed("Master to Slave"), direction_allowed("Master to Slave", True)
			)

	def test_shared_is_explicit_and_missing_branch_is_not_broadcast(self):
		config = frappe._dict(doctype_name="Sales Invoice", scope="Branch", branch_field="branch")
		with patch("arab_streaming.streaming.policy._values", return_value=[]):
			self.assertFalse(matches_scope({"doctype": "Sales Invoice"}, config, "A"))
			with self.assertRaises(frappe.ValidationError):
				matches_scope({"doctype": "Sales Invoice"}, config, "A", True)
		config.scope = "Shared"
		self.assertTrue(matches_scope({"doctype": "Item"}, config, "A"))

	def test_rule_revision_and_no_code_addition_for_business_type(self):
		doc = frappe.get_doc(
			dict(
				doctype="Streaming Rule",
				doctype_name="Customer",
				scope="Shared",
				direction="Both",
				enabled=1,
				all_branches=1,
			)
		).insert()
		self.assertEqual(doc.revision, 1)
		self.assertTrue(frappe.db.exists("Streaming Rule Revision", {"rule": doc.name, "revision": 1}))
		doc.conditions = "doc.customer_type == 'Company'"
		doc.save()
		self.assertEqual(doc.revision, 2)
		self.assertTrue(frappe.db.exists("Streaming Rule Revision", {"rule": doc.name, "revision": 2}))

	def test_submittable_shared_rule_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				dict(
					doctype="Streaming Rule",
					doctype_name="Sales Invoice",
					scope="Shared",
					direction="Both",
					enabled=1,
					all_branches=1,
				)
			).insert()
