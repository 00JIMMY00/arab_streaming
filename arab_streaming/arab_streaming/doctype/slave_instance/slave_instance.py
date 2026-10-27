import frappe
from frappe.model.document import Document

from arab_streaming.streaming.configuration import require_role
from arab_streaming.streaming.defaults import get_default_rows
from arab_streaming.streaming.guards import protect_fields, require_system_write
from arab_streaming.streaming.validation import validate_json_object, validate_rules


class SlaveInstance(Document):
	def before_insert(self):
		require_role("Master")
		require_system_write()
		if not self.syncing_doctypes:
			for row in get_default_rows("syncing", "Master"):
				self.append("syncing_doctypes", row)

	def validate(self):
		require_role("Master")
		protect_fields(
			self,
			(
				"restaurant_branch",
				"domain",
				"api_key",
				"connection_status",
				"enabled",
				"streaming_version",
				"metadata",
				"receiving_doctypes_data",
			),
		)
		validate_json_object(self.metadata)
		validate_rules(self)

	def on_trash(self):
		require_system_write()
