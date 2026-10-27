import secrets
import string

import frappe
from frappe.model.document import Document

from arab_streaming.streaming.guards import require_administrator, system_write_active


def generate_token():
	return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(15))


class InstanceKey(Document):
	def before_insert(self):
		require_administrator()
		# The unique database index is the final concurrent-collision guard.
		for _ in range(10):
			self.api_key = generate_token()
			if not frappe.db.exists("Instance Key", {"api_key": self.api_key}):
				break
		else:
			frappe.throw("Unable to allocate an Instance Key; retry the operation")
		self.api_secret = generate_token()

	def validate(self):
		if (self.enabled and not frappe.db.get_value("User", self.user, "enabled")) or self.user in (
			"Administrator",
			"Guest",
		):
			frappe.throw(
				"Instance Key requires an enabled integration User other than Administrator or Guest"
			)
		previous = self.get_doc_before_save()
		if previous and (self.api_key != previous.api_key or self.api_secret != previous.api_secret):
			frappe.throw("Instance Key credentials are immutable; create a replacement key")
		if previous and self.user != previous.user:
			require_administrator()

	@frappe.whitelist()
	def reveal_secret(self):
		require_administrator()
		return self.get_password("api_secret")

	def get_password(self, fieldname="api_secret", raise_exception=True):
		# frappe.client.get_password delegates here, so the same guard covers
		# generic Password reveal as well as our explicit form action.
		if not system_write_active():
			require_administrator()
		return super().get_password(fieldname, raise_exception=raise_exception)

	def on_trash(self):
		require_administrator()
		if frappe.db.exists("Slave Instance", {"api_key": self.api_key, "enabled": 1}):
			frappe.throw("An enabled Slave Instance still uses this key")
