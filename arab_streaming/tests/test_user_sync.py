from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.protocol.codec import snapshot
from arab_streaming.streaming.adapters import LOCAL_FIELDS, SAFE_TYPES, validate_document
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.dependencies import freeze
from arab_streaming.streaming.executor import apply_document
from arab_streaming.streaming.permissions import permission_types, provision
from arab_streaming.streaming.protected import freeze as freeze_passwords
from arab_streaming.streaming.protected import import_values


class TestUserSync(FrappeTestCase):
	def setUp(self):
		self.previous_user = frappe.session.user
		self.previous_enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_enabled = False
		frappe.db.savepoint("user_sync_case")
		self.executor = frappe.get_doc(
			dict(
				doctype="User",
				email="sync-executor-" + frappe.generate_hash(length=10) + "@example.invalid",
				first_name="Executor",
				enabled=1,
				send_welcome_email=0,
			)
		).insert()
		provision(self.executor.name, [dict(doctype_name="User", enabled=1)])

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="user_sync_case")
		frappe.conf.arab_streaming_enabled = self.previous_enabled
		frappe.set_user(self.previous_user)

	def source_user(self):
		email = "sync-cashier-" + frappe.generate_hash(length=10) + "@example.invalid"
		doc = frappe.get_doc(
			dict(
				doctype="User",
				name=email,
				email=email,
				first_name="Cashier",
				enabled=1,
				user_type="System User",
				send_welcome_email=1,
				roles=[dict(role="Accounts User")],
			)
		)
		for child in doc.get_all_children():
			child.name = frappe.generate_hash(length=10)
		return doc

	def test_user_create_update_roles_and_local_credentials(self):
		source = self.source_user()
		source.api_key = "source-key"
		source.new_password = "source-password"
		source.reset_password_key = "source-reset"
		document = snapshot(source)
		self.assertIn("User", SAFE_TYPES)
		for name in LOCAL_FIELDS["User"]:
			self.assertNotIn(name, document)
		self.assertIsNone(freeze_passwords(document))
		self.assertEqual(import_values({}, document, "unused-export"), [])
		validate_document(document)
		with replay("user-sync-test", self.executor.name):
			with patch("frappe.core.doctype.user.user.User.send_welcome_mail_to_user") as mail:
				self.assertEqual(apply_document(frappe._dict(), document)[0], "Success")
				mail.assert_not_called()
			created = frappe.get_doc("User", source.name)
			self.assertEqual(created.first_name, "Cashier")
			self.assertIn("Accounts User", [r.role for r in created.roles])
			frappe.db.set_value("User", created.name, "api_key", "local-key")
			document["first_name"] = "Updated Cashier"
			self.assertEqual(apply_document(frappe._dict(), document)[0], "Success")
			self.assertEqual(frappe.db.get_value("User", created.name, "api_key"), "local-key")
			self.assertEqual(frappe.db.get_value("User", created.name, "first_name"), "Updated Cashier")
		for field in ("api_key", "reset_password_key", "social_logins"):
			with self.subTest(field=field):
				with self.assertRaises(frappe.ValidationError):
					validate_document(dict(document, **{field: "invalid"}))

	def test_user_dependency_creates_roles_without_user_root_rule(self):
		provision(self.executor.name, [dict(doctype_name="POS Opening Entry", enabled=1)])
		source = self.source_user()
		with replay("user-dependency-test", self.executor.name):
			self.assertEqual(apply_document(frappe._dict(), snapshot(source))[0], "Success")
			created = frappe.get_doc("User", source.name)
			self.assertIn("Accounts User", [r.role for r in created.roles])
			self.assertEqual(created.user_type, "System User")
			self.assertFalse(frappe.has_permission("User", "write", doc=created))

	def test_opening_captures_user_and_provisions_dependency_permissions(self):
		source = self.source_user()
		source.send_welcome_email = 0
		source.insert()
		versions = freeze(dict(doctype="POS Opening Entry", name="opening-fixture", user=source.name))
		self.assertIn(("User", source.name), [(dt, name) for dt, name, *_ in versions])
		_roots, types = permission_types([dict(doctype_name="POS Opening Entry", enabled=1)])
		self.assertIn("User", types)
		self.assertNotIn("Role", types)
