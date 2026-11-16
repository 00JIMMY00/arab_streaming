import json
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.password import check_password, update_password

from arab_streaming.streaming import user_passwords as passwords
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.permissions import provision


class TestUserPasswords(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.role = frappe.conf.get("arab_streaming_site_role")
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		self.admin_enabled = frappe.conf.get("arab_streaming_sync_administrator_password")
		frappe.conf.arab_streaming_sync_administrator_password = 0
		frappe.conf.arab_streaming_enabled = False
		frappe.db.savepoint("password_case")
		self.user = frappe.get_doc(
			dict(
				doctype="User",
				email="pwd-audit-" + frappe.generate_hash(length=10) + "@example.invalid",
				first_name="Password Test",
				send_welcome_email=0,
			)
		).insert()
		self.secret = frappe.generate_hash(length=24)
		update_password(self.user.name, self.secret)
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.conf.arab_streaming_enabled = True

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="password_case")
		frappe.conf.arab_streaming_site_role = self.role
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.conf.arab_streaming_sync_administrator_password = self.admin_enabled

	def test_opt_in_capture_encrypts_hash_and_keeps_event_clean(self):
		selected = (
			frappe._dict(doctype="Slave Instance", name="p"),
			frappe._dict(),
			{"include_passwords": 1},
		)
		with (
			patch("arab_streaming.streaming.outbox.fence"),
			patch("arab_streaming.streaming.outbox.recipients", return_value=[selected]),
			patch("arab_streaming.streaming.outbox.store_event") as store,
		):
			passwords.capture(self.user)
			data = store.call_args.kwargs["data"]
			raw = json.dumps(data)
			self.assertNotIn(self.secret, raw)
			self.assertNotIn("$pbkdf2", raw)
			version = frappe.get_doc("Streaming Protected Version", data["user_password_version"])
			self.assertTrue(
				json.loads(version.get_password("secret_data"))["hash"].startswith(("$pbkdf2", "$argon2"))
			)
			store.reset_mock()
			passwords.capture(self.user)
			store.assert_not_called()

	def test_disabled_rule_does_not_capture_credentials(self):
		with (
			patch("arab_streaming.streaming.outbox.fence"),
			patch("arab_streaming.streaming.outbox.recipients", return_value=[]),
			patch("arab_streaming.streaming.outbox.store_event") as store,
		):
			passwords.capture(self.user)
			store.assert_not_called()

	def test_new_receiver_gets_current_hash_without_resending_to_existing_peers(self):
		selected = [
			(frappe._dict(doctype="Slave Instance", name=name), frappe._dict(), {"include_passwords": 1})
			for name in ("old", "new")
		]
		with (
			patch("arab_streaming.streaming.outbox.fence"),
			patch("arab_streaming.streaming.outbox.recipients", return_value=selected),
			patch("arab_streaming.streaming.outbox.store_event") as store,
		):
			passwords.capture(self.user)
			store.reset_mock()
			passwords.capture(self.user, destination="new")
			self.assertEqual([item[0].name for item in store.call_args.args[2]], ["new"])

	def test_stale_registration_job_cannot_seed_replacement_pair(self):
		with (
			patch(
				"frappe.get_doc",
				return_value=frappe._dict(
					doctype="Slave Instance", name="peer", enabled=1, connection_status="Connected"
				),
			),
			patch(
				"arab_streaming.streaming.connections.peer_state",
				return_value=frappe._dict(pairing_id="replacement", pairing_generation=2),
			),
			patch.object(passwords, "capture") as capture,
		):
			passwords.seed_receiver("peer", "old", 1)
		capture.assert_not_called()

	def test_builtin_and_integration_identities_are_excluded(self):
		for user in ["Administrator", "Guest", "x@rms.invalid", "x@arab-erp.invalid"]:
			self.assertFalse(passwords.eligible(user))
		frappe.conf.arab_streaming_sync_administrator_password = 1
		self.assertTrue(passwords.eligible("Administrator"))
		self.assertFalse(passwords.eligible("Guest"))

	def admin_data(self):
		return dict(
			document={"doctype": "User", "name": "Administrator", "docstatus": 0},
			user_password_version="v",
			administrator_password_only=1,
		)

	def test_administrator_payload_requires_explicit_opt_in_and_no_profile_fields(self):
		data = self.admin_data()
		with self.assertRaises(frappe.PermissionError):
			passwords.administrator_event("Administrator", data)
		frappe.conf.arab_streaming_sync_administrator_password = 1
		self.assertTrue(passwords.administrator_event("Administrator", data))
		data["document"]["roles"] = []
		with self.assertRaises(frappe.PermissionError):
			passwords.administrator_event("Administrator", data)
		with self.assertRaises(frappe.PermissionError):
			passwords.administrator_event(self.user.name, self.admin_data())

	def test_administrator_capture_freezes_only_password_identity(self):
		frappe.conf.arab_streaming_sync_administrator_password = 1
		selected = (
			frappe._dict(doctype="Slave Instance", name="p"),
			frappe._dict(),
			{"include_passwords": 1},
		)
		with (
			patch("arab_streaming.streaming.outbox.fence"),
			patch("arab_streaming.streaming.outbox.recipients", return_value=[selected]) as recipients,
			patch("arab_streaming.streaming.outbox.store_event") as store,
		):
			passwords.capture(frappe.get_doc("User", "Administrator"))
			self.assertEqual(recipients.call_args.args[1], "administrator_password")
			self.assertTrue(passwords.administrator_event("Administrator", store.call_args.kwargs["data"]))
			self.assertTrue(store.call_args.kwargs["_related_prepared"])
			self.assertEqual(store.call_args.kwargs["_frozen_versions"], [])

	def test_administrator_apply_uses_paired_password_capability_without_user_profile_access(self):
		from frappe.utils.password import passlibctx

		frappe.conf.arab_streaming_sync_administrator_password = 1
		frappe.conf.arab_streaming_site_role = "Slave"
		peer = frappe._dict(doctype="Master Instance", name="peer")
		cap = frappe._dict(
			reference_doctype="User", reference_name="Administrator", remote_dispatch_event="event"
		)
		new_password = frappe.generate_hash(length=24)
		hashed = passlibctx.hash(new_password)
		before = frappe.db.get_value(
			"User", "Administrator", ["api_key", "first_name", "enabled"], as_dict=True
		)
		with (
			patch(
				"arab_streaming.streaming.policy.config_for", return_value=frappe._dict(include_passwords=1)
			),
			patch("arab_streaming.streaming.mapping.mapped_name", return_value="Administrator"),
			patch(
				"arab_streaming.streaming.connections.peer_state",
				return_value=frappe._dict(pairing_id="pair", execution_user=self.user.name),
			),
			patch("arab_streaming.streaming.connections.wire_identity", return_value={}),
			patch("arab_streaming.streaming.client.remote_call", return_value={"hash": hashed}) as remote,
			replay("pair", self.user.name),
		):
			self.assertFalse(
				frappe.has_permission("User", "write", doc=frappe.get_doc("User", "Administrator"))
			)
			passwords.apply(peer, cap, self.admin_data())
			passwords.apply(peer, cap, self.admin_data())
			self.assertEqual(remote.call_count, 2)
		self.assertEqual(check_password("Administrator", new_password), "Administrator")
		self.assertEqual(frappe.db.get_value("User", "Administrator", list(before), as_dict=True), before)

	def test_administrator_apply_rejects_another_replay_pair(self):
		frappe.conf.arab_streaming_sync_administrator_password = 1
		frappe.conf.arab_streaming_site_role = "Slave"
		cap = frappe._dict(reference_doctype="User", reference_name="Administrator")
		with (
			patch(
				"arab_streaming.streaming.policy.config_for", return_value=frappe._dict(include_passwords=1)
			),
			patch("arab_streaming.streaming.mapping.mapped_name", return_value="Administrator"),
			patch(
				"arab_streaming.streaming.connections.peer_state",
				return_value=frappe._dict(pairing_id="other", execution_user=self.user.name),
			),
			replay("pair", self.user.name),
		):
			with self.assertRaises(frappe.PermissionError):
				passwords.apply(frappe._dict(doctype="Master Instance", name="peer"), cap, self.admin_data())

	def test_fetch_wrong_peer_or_direction_is_denied(self):
		with self.assertRaises(frappe.PermissionError):
			passwords.fetch(frappe._dict(doctype="Master Instance"), "none", "none")
		with patch(
			"frappe.get_doc",
			return_value=frappe._dict(
				reference_doctype="User",
				reference_name=self.user.name,
				data=json.dumps({"user_password_version": "v"}),
				instances=[],
			),
		):
			with self.assertRaises(frappe.PermissionError):
				passwords.fetch(frappe._dict(doctype="Slave Instance", name="other"), "event", "v")

	def test_master_password_applies_without_changing_api_credentials(self):
		frappe.conf.arab_streaming_enabled = False
		provision(self.user.name, [dict(doctype_name="User", enabled=1)])
		frappe.db.set_value("User", self.user.name, "api_key", "local-api-key")
		from frappe.utils.password import passlibctx

		new_password = frappe.generate_hash(length=24)
		hashed = passlibctx.hash(new_password)
		frappe.conf.arab_streaming_site_role = "Slave"
		peer = frappe._dict(doctype="Master Instance", name="peer")
		cap = frappe._dict(
			reference_doctype="User", reference_name=self.user.name, remote_dispatch_event="event"
		)
		with (
			patch(
				"arab_streaming.streaming.policy.config_for", return_value=frappe._dict(include_passwords=1)
			),
			patch("arab_streaming.streaming.mapping.mapped_name", return_value=self.user.name),
			patch("arab_streaming.streaming.connections.wire_identity", return_value={}),
			patch("arab_streaming.streaming.client.remote_call", return_value={"hash": hashed}),
			replay("pair", self.user.name),
		):
			passwords.apply(peer, cap, {"user_password_version": "v"})
		self.assertEqual(check_password(self.user.name, new_password), self.user.name)
		with self.assertRaises(frappe.AuthenticationError):
			check_password(self.user.name, self.secret)
		self.assertEqual(frappe.db.get_value("User", self.user.name, "api_key"), "local-api-key")
