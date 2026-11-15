import unittest
from unittest.mock import Mock, patch

import frappe

from arab_streaming.streaming.executor import defer_initial_links


class TestBootstrapLinks(unittest.TestCase):
	def test_user_snapshots_exclude_local_execution_roles_and_keep_business_roles(self):
		from arab_streaming.protocol.codec import snapshot

		child_meta = frappe._dict(fields=[frappe._dict(fieldname="role", fieldtype="Link")])
		roles = [
			frappe._dict(doctype="Has Role", name=str(i), docstatus=0, role=role, meta=child_meta)
			for i, role in enumerate(("Arab Streaming 0123456789ab", "Accounts User"))
		]
		user = frappe._dict(
			doctype="User",
			name="test@example.invalid",
			docstatus=0,
			roles=roles,
			meta=frappe._dict(fields=[frappe._dict(fieldname="roles", fieldtype="Table")]),
		)
		with patch("arab_streaming.protocol.codec.transferable_fields", side_effect=lambda meta: meta.fields):
			data = snapshot(user)
		self.assertEqual([r["role"] for r in data["roles"]], ["Accounts User"])

	def test_controller_permissions_are_limited_to_enabled_price_list_rules(self):
		from arab_streaming.streaming.permissions import controller_writes, permission_types

		self.assertEqual(controller_writes({"Item"}), set())
		self.assertEqual(controller_writes({"Price List"}), {"Buying Settings", "Selling Settings"})
		with patch("frappe.get_meta", return_value=frappe._dict(fields=[])):
			roots, types = permission_types([dict(doctype_name="Price List", enabled=0)])
			self.assertEqual(roots, set())
			self.assertEqual(types, set())
			roots, types = permission_types([dict(doctype_name="Price List", enabled=1)])
			self.assertTrue({"Buying Settings", "Selling Settings"}.issubset(types))
			self.assertEqual(roots, {"Price List"})

	def test_optional_link_is_retained_for_transactional_restoration(self):
		source = dict(doctype="Store", name="S1", area="A1")
		document, deferred = dict(source), {}
		meta = Mock()
		meta.get_field.return_value = frappe._dict(fieldtype="Link", reqd=0)
		with (
			patch("frappe.get_hooks", return_value=["installed.hook"]),
			patch("frappe.get_attr", return_value=lambda *args: ["area"]),
		):
			defer_initial_links({}, source, document, meta, deferred)
		self.assertIsNone(document["area"])
		self.assertEqual(source["area"], "A1")
		self.assertEqual(deferred[("Store", "S1")], source)

	def test_mandatory_or_non_link_fields_are_never_deferred(self):
		def reject(message):
			raise frappe.ValidationError(message)

		for field in (None, frappe._dict(fieldtype="Data", reqd=0), frappe._dict(fieldtype="Link", reqd=1)):
			meta = Mock()
			meta.get_field.return_value = field
			with (
				patch("frappe.get_hooks", return_value=["installed.hook"]),
				patch("frappe.get_attr", return_value=lambda *args: ["area"]),
				patch("frappe.throw", side_effect=reject),
			):
				with self.assertRaises(frappe.ValidationError):
					defer_initial_links({}, {"doctype": "Store", "name": "S1"}, {"area": "A1"}, meta, {})

	def test_no_consumer_hook_leaves_the_document_unchanged(self):
		document = dict(doctype="Store", name="S1", area="A1")
		with patch("frappe.get_hooks", return_value=[]):
			defer_initial_links({}, dict(document), document, Mock(), {})
		self.assertEqual(document["area"], "A1")
