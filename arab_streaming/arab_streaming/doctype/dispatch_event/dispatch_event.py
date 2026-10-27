import frappe

from arab_streaming.streaming.guards import ManagedDocument, require_immutable_fields
from arab_streaming.streaming.status import dispatch_status
from arab_streaming.streaming.validation import validate_json_object


class DispatchEvent(ManagedDocument):
	def validate(self):
		super().validate()
		validate_json_object(self.metadata)
		require_immutable_fields(
			self,
			(
				"reference_doctype",
				"reference_name",
				"data",
				"metadata",
				"event_type",
				"created_on",
				"event_sequence",
				"created_by",
				"document_event",
				"payload_digest",
			),
		)
		previous = self.get_doc_before_save()
		if previous:
			if [row.name for row in self.instances] != [row.name for row in previous.instances]:
				frappe.throw("Historical dispatch destinations cannot be replaced or reordered")
			for row, old in zip(self.instances, previous.instances, strict=True):
				row._doc_before_save = old
				require_immutable_fields(
					row,
					(
						"instance_type",
						"instance_name",
						"doctype_config",
						"metadata",
						"pairing_id",
						"pairing_generation",
						"payload_digest",
					),
				)
		seen = set()
		for row in self.instances:
			if row.instance_type not in ("Master Instance", "Slave Instance"):
				frappe.throw("Invalid streaming destination type")
			identity = (row.instance_type, row.instance_name)
			if identity in seen:
				frappe.throw("Duplicate streaming destination")
			seen.add(identity)
			validate_json_object(row.metadata)
		self.status = dispatch_status(self.instances)
