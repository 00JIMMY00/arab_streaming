import frappe

from arab_streaming.api.connection import authorize
from arab_streaming.streaming.connections import bound_peer
from arab_streaming.streaming.inbox import ingest
from arab_streaming.streaming.transport import acknowledge_for, pending_for


def addressed(protocol, version, pairing_id, source_installation_id, pairing_generation):
	from arab_streaming.auth import require_wire_auth
	from arab_streaming.streaming.connections import validate_version

	validate_version(protocol, version)
	return bound_peer(require_wire_auth(), pairing_id, source_installation_id, pairing_generation)


@frappe.whitelist(methods=["POST"])
def receive(
	protocol, version, pairing_id, source_installation_id, pairing_generation, envelopes, source_head=None
):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	receipts = []
	errors = []
	if not isinstance(envelopes, list) or not 1 <= len(envelopes) <= 50:
		frappe.throw("Invalid event batch")
	for item in envelopes:
		frappe.db.savepoint("streaming_receive_item")
		try:
			receipts.extend(ingest(peer, state, [item], source_head=source_head))
		except Exception as exc:
			frappe.db.rollback(save_point="streaming_receive_item")
			errors.append(
				{
					"event_id": item.get("event_id") if isinstance(item, dict) else None,
					"error": str(exc)[:1200],
				}
			)
	return {"receipts": receipts, "errors": errors}


@frappe.whitelist(methods=["POST"])
def pending(protocol, version, pairing_id, source_installation_id, pairing_generation, limit=25):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.transport import source_head

	return {"envelopes": pending_for(peer, limit), "source_head": source_head()}


@frappe.whitelist(methods=["POST"])
def acknowledge(protocol, version, pairing_id, source_installation_id, pairing_generation, receipts):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	acknowledge_for(peer, receipts)
	return {"acknowledged": len(receipts)}


@frappe.whitelist(methods=["POST"])
def results(protocol, version, pairing_id, source_installation_id, pairing_generation, events):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.feedback import receiver_results

	return receiver_results(peer, events)


@frappe.whitelist(methods=["POST"])
def retry(
	protocol, version, pairing_id, source_installation_id, pairing_generation, event_id, payload_digest
):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.feedback import capture_for, receipt_for, request_retry

	receipt = receipt_for(peer, event_id, payload_digest)
	capture = capture_for(peer, receipt, lock=True)
	if not capture:
		frappe.throw("The receiving capture is no longer available for retry")
	return request_retry(capture)
