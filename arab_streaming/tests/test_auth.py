from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.auth import authenticate_credentials, authenticate_request, require_wire_auth


class TestStreamingAuth(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("streaming_auth")
		self.user = frappe.get_doc(
			dict(
				doctype="User",
				email="stream-auth-" + frappe.generate_hash(length=8) + "@example.invalid",
				first_name="Streaming auth",
				enabled=1,
				send_welcome_email=0,
			)
		).insert()
		self.key = frappe.get_doc(dict(doctype="Instance Key", user=self.user.name)).insert()
		self.secret = self.key.get_password("api_secret")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.arab_streaming_key = None
		frappe.db.rollback(save_point="streaming_auth")

	def test_credentials_disabled_key_and_disabled_user(self):
		self.assertEqual(authenticate_credentials(self.key.api_key, self.secret).user, self.user.name)
		for key, secret in (("bad", self.secret), (self.key.api_key, "x" * 15), (self.key.api_key, None)):
			with self.assertRaises(frappe.AuthenticationError):
				authenticate_credentials(key, secret)
		frappe.db.set_value("Instance Key", self.key.name, "enabled", 0)
		with self.assertRaises(frappe.AuthenticationError):
			authenticate_credentials(self.key.api_key, self.secret)
		frappe.db.set_value("Instance Key", self.key.name, "enabled", 1)
		frappe.db.set_value("User", self.user.name, "enabled", 0)
		with self.assertRaises(frappe.AuthenticationError):
			authenticate_credentials(self.key.api_key, self.secret)

	def test_regular_session_cannot_call_wire_api(self):
		frappe.local.arab_streaming_key = None
		with self.assertRaises(frappe.AuthenticationError):
			require_wire_auth()

	def test_instance_credentials_cannot_authenticate_generic_document_api(self):
		header = f"ArabStreaming {self.key.api_key}:{self.secret}"
		with patch(
			"frappe.get_request_header",
			side_effect=lambda name, default=None: header if name == "Authorization" else default,
		):
			with patch.object(frappe.local, "request", frappe._dict(path="/api/resource/User"), create=True):
				with self.assertRaises(frappe.AuthenticationError):
					authenticate_request()
