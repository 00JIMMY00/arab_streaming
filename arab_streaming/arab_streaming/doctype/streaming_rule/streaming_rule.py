from frappe.model.document import Document

from arab_streaming.streaming.policy import record_revision, validate_rule


class StreamingRule(Document):
	def validate(self):
		validate_rule(self)

	def on_update(self):
		record_revision(self)

	def on_trash(self):
		import frappe

		if self.enabled:
			frappe.throw("Disable this rule before deleting it; immutable revisions are retained.")
