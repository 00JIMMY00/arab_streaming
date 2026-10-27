import frappe

from arab_streaming.streaming.guards import ManagedDocument, require_immutable_fields
from arab_streaming.streaming.validation import validate_json_object


class CaptureEvent(ManagedDocument):
	def validate(self):
		super().validate()
		if self.instance_type not in ("Master Instance", "Slave Instance"):
			frappe.throw("Invalid streaming source type")
		validate_json_object(self.metadata)
		require_immutable_fields(
			self,
			(
				"instance_type",
				"instance_name",
				"event_type",
				"reference_doctype",
				"reference_name",
				"data",
				"metadata",
				"doctype_config",
				"remote_dispatch_event",
				"created_on",
				"event_sequence",
				"created_by",
				"document_event",
				"source_installation_id",
				"pairing_id",
				"pairing_generation",
				"payload_digest",
				"priority",
			),
		)
