import frappe
from frappe.model.document import Document

from arab_streaming.streaming.adapters import require_supported


class StreamingValueMapping(Document):
	def validate(self):
		require_supported(self.reference_doctype)
		if self.instance_type not in ("Master Instance", "Slave Instance"):
			frappe.throw("Select a paired connection")
		if not frappe.db.exists(self.reference_doctype, self.local_name):
			frappe.throw("The mapped local record must exist")
