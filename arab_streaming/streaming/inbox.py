import json
from datetime import timezone

import frappe

from arab_streaming.protocol.envelope import decode_batch
from arab_streaming.streaming.adapters import require_supported
from arab_streaming.streaming.guards import system_write


def ingest(peer, state, envelopes, source_head=None):
	# Serialize this source's receipt identity checks; the UNIQUE keys remain final guards.
	frappe.db.sql("SELECT name FROM `tabStreaming Peer State` WHERE name=%s FOR UPDATE", (state.name,))
	state = frappe.get_doc("Streaming Peer State", state.name, for_update=True)
	batch = decode_batch(json.dumps(envelopes, ensure_ascii=False))
	result = []
	with system_write():
		for envelope in batch:
			if (
				envelope["source_installation_id"] != state.remote_installation_id
				or envelope["pairing_id"] != state.pairing_id
				or envelope["pairing_generation"] != str(state.pairing_generation)
			):
				frappe.throw("Event source does not match the authenticated pairing", frappe.PermissionError)
			require_supported(envelope["reference_doctype"])
			config = envelope["doctype_config"]
			from arab_streaming.streaming.policy import validate_captured_configuration

			validate_captured_configuration(config)
			if (
				config.get("doctype_name") != envelope["reference_doctype"]
				or config.get("ignore_validation")
				or config.get("directly_inserted_in_db")
				or (config.get("on_duplicated_insert") or "") not in ("", "Update", "Skip", "Throw Error")
			):
				frappe.throw("Unsupported event configuration")
			if config.get("rule_id") and not config["rule_id"].startswith("legacy:"):
				from arab_streaming.streaming.policy import authorize_document, config_for

				rule = config_for(peer, envelope["reference_doctype"], incoming=True)
				if (
					not rule
					or rule.name != config["rule_id"]
					or int(config.get("rule_revision") or 0) > rule.revision
				):
					frappe.throw(
						"Refresh the published rule before receiving this event", frappe.PermissionError
					)
				document = envelope["data"].get("document")
				if not isinstance(document, dict):
					frappe.throw("A business snapshot is required")
				authorize_document(peer, document, incoming=True, config=rule)
			identity = dict(
				source_installation_id=state.remote_installation_id,
				pairing_id=state.pairing_id,
				pairing_generation=state.pairing_generation,
				remote_dispatch_event=envelope["event_id"],
			)
			old = frappe.db.get_value(
				"Streaming Event Receipt", identity, ["name", "payload_digest", "capture_event"], as_dict=True
			)
			if old:
				if old.payload_digest != envelope["payload_digest"]:
					frappe.throw("An existing event identity was received with a different digest")
				result.append(
					{
						"event_id": envelope["event_id"],
						"capture_event": old.capture_event,
						"payload_digest": old.payload_digest,
					}
				)
				continue
			if source_head is not None:
				if (
					not isinstance(source_head, str)
					or not source_head.isdecimal()
					or len(source_head) > 19
					or not 0 <= int(source_head) < 2**63
				):
					frappe.throw("Invalid committed source head")
				head = int(source_head)
				if (
					head < max(state.get("observed_source_head") or 0, state.received_through or 0)
					or int(envelope["event_sequence"]) > head
				):
					frappe.throw(
						"Source head moved backwards; reconcile a possible source restore before continuing"
					)
				state.observed_source_head = max(head, state.get("observed_source_head") or 0)
			if source_head is None and int(envelope["event_sequence"]) <= (state.received_through or 0):
				frappe.throw(
					"Source sequence moved backwards or was reused; reconcile source restore/identity before continuing"
				)
			capture = frappe.get_doc(
				dict(
					doctype="Capture Event",
					instance_type=peer.doctype,
					instance_name=peer.name,
					event_type=envelope["event_type"],
					reference_doctype=envelope["reference_doctype"],
					reference_name=envelope["reference_name"],
					data=json.dumps(envelope["data"], ensure_ascii=False),
					metadata=envelope["metadata"],
					doctype_config=json.dumps(config, ensure_ascii=False),
					created_on=frappe.utils.get_datetime(envelope["created_on"])
					.astimezone(timezone.utc)
					.replace(tzinfo=None),
					event_sequence=int(envelope["event_sequence"]),
					created_by=envelope["created_by"],
					document_event=envelope["document_event"],
					status="Pending",
					payload_digest=envelope["payload_digest"],
					priority=int(100 if config.get("priority") is None else config["priority"]),
					**identity,
				)
			)
			capture.insert(ignore_permissions=True)
			receipt = frappe.get_doc(
				dict(
					doctype="Streaming Event Receipt",
					**identity,
					payload_digest=envelope["payload_digest"],
					capture_event=capture.name,
					event_sequence=int(envelope["event_sequence"]),
					received_on=frappe.utils.now_datetime(),
				)
			)
			receipt.insert(ignore_permissions=True)
			state.received_through = max(state.received_through or 0, int(envelope["event_sequence"]))
			state.save(ignore_permissions=True)
			result.append(
				{
					"event_id": envelope["event_id"],
					"capture_event": capture.name,
					"payload_digest": envelope["payload_digest"],
				}
			)
	from arab_streaming.tasks import wakeup

	frappe.db.after_commit.add(wakeup)
	return result
