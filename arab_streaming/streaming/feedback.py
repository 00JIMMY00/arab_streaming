"""Peer-bound receiver results, separate from durable delivery receipts."""

import frappe

from arab_streaming.streaming.connections import peer_state, wire_identity
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.status import dispatch_status

TERMINAL = ("Success", "Skipped", "Ignored")
RESULTS = (*TERMINAL, "Pending", "Failed", "Unavailable")


def receipt_for(peer, event_id, payload_digest):
	if not isinstance(event_id, str) or not 1 <= len(event_id) <= 140:
		frappe.throw("Invalid event identity")
	if not isinstance(payload_digest, str) or len(payload_digest) != 64:
		frappe.throw("Invalid event digest")
	state = peer_state(peer.doctype, peer.name)
	receipt = frappe.db.get_value(
		"Streaming Event Receipt",
		{
			"source_installation_id": state.remote_installation_id,
			"pairing_id": state.pairing_id,
			"pairing_generation": state.pairing_generation,
			"remote_dispatch_event": event_id,
			"payload_digest": payload_digest,
		},
		["name", "capture_event", "application_status", "remote_dispatch_event", "payload_digest"],
		as_dict=True,
	)
	if not receipt:
		frappe.throw("Event receipt does not belong to this peer", frappe.PermissionError)
	return receipt


def capture_for(peer, receipt, lock=False):
	if not receipt.capture_event or not frappe.db.exists("Capture Event", receipt.capture_event):
		return None
	capture = frappe.get_doc("Capture Event", receipt.capture_event, for_update=lock)
	state = peer_state(peer.doctype, peer.name)
	if (
		capture.instance_type != peer.doctype
		or capture.instance_name != peer.name
		or capture.pairing_id != state.pairing_id
		or capture.pairing_generation != state.pairing_generation
		or capture.source_installation_id != state.remote_installation_id
		or capture.remote_dispatch_event != receipt.remote_dispatch_event
		or capture.payload_digest != receipt.payload_digest
	):
		frappe.throw("Capture does not belong to this peer", frappe.PermissionError)
	return capture


def receiver_results(peer, events):
	if not isinstance(events, list) or not 1 <= len(events) <= 50:
		frappe.throw("Invalid receiver-result batch")
	results = []
	for item in events:
		if not isinstance(item, dict):
			frappe.throw("Invalid receiver-result identity")
		receipt = receipt_for(peer, item.get("event_id"), item.get("payload_digest"))
		capture = capture_for(peer, receipt)
		results.append(
			dict(
				event_id=item["event_id"],
				payload_digest=item["payload_digest"],
				capture_event=receipt.capture_event,
				status=capture.status
				if capture
				else receipt.application_status
				if receipt.application_status in TERMINAL
				else "Unavailable",
				exception=(capture.exception or "")[:2000] if capture else None,
				retries=capture.retries if capture else None,
				next_attempt_at=str(capture.next_attempt_at) if capture and capture.next_attempt_at else None,
			)
		)
	return {"results": results}


def remember_result(capture):
	# Kept with the durable receipt after successful capture history is cleaned.
	if capture.status in TERMINAL:
		frappe.db.set_value(
			"Streaming Event Receipt",
			{
				"source_installation_id": capture.source_installation_id,
				"pairing_id": capture.pairing_id,
				"pairing_generation": capture.pairing_generation,
				"remote_dispatch_event": capture.remote_dispatch_event,
				"capture_event": capture.name,
			},
			"application_status",
			capture.status,
			update_modified=False,
		)


def request_retry(capture):
	if capture.status != "Failed" or capture.ignore:
		frappe.throw("Retry is available only for a Failed Capture Event")
	peer = frappe.get_doc(capture.instance_type, capture.instance_name)
	if not peer.enabled:
		frappe.throw("Enable this connection before retrying its capture")
	frappe.db.set_value("Capture Event", capture.name, {"status": "Pending", "next_attempt_at": None})
	frappe.enqueue(
		"arab_streaming.tasks.execute_only",
		only_peer=peer.name,
		enqueue_after_commit=True,
		job_id="streaming-retry-" + peer.name,
		deduplicate=True,
	)
	return {"queued": True, "capture_event": capture.name, "status": "Pending"}


def refresh_results(peer, event_name=None):
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.transport import envelope

	state = peer_state(peer.doctype, peer.name)
	conditions = (
		"AND child.parent=%s"
		if event_name
		else """AND (child.receiver_status IS NULL
		OR child.receiver_status NOT IN ('Success','Skipped','Ignored','Unavailable'))
		AND (child.status_checked_at IS NULL OR child.status_checked_at<%s)"""
	)
	values = [peer.doctype, peer.name, state.pairing_id, state.pairing_generation]
	values.append(
		event_name if event_name else frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-5)
	)
	rows = frappe.db.sql(
		f"""SELECT child.name, child.parent FROM `tabDispatch Event Instance` child
		WHERE child.instance_type=%s AND child.instance_name=%s
		AND child.pairing_id=%s AND child.pairing_generation=%s
		AND child.synced=1 AND child.`ignore`=0 {conditions}
		ORDER BY COALESCE(child.status_checked_at, '1970-01-01'), child.creation LIMIT 25""",
		values,
		as_dict=True,
	)
	if not rows:
		return
	expected = {}
	for row in rows:
		event = frappe.get_doc("Dispatch Event", row.parent)
		destination = next(r for r in event.instances if r.name == row.name)
		expected[event.name] = dict(
			event_id=event.name, payload_digest=envelope(event, destination)["payload_digest"]
		)
	response = remote_call(
		peer, "arab_streaming.api.events.results", **wire_identity(peer), events=list(expected.values())
	)
	items = response.get("results")
	if not isinstance(items, list) or len(items) != len(expected):
		frappe.throw("Receiver returned an incomplete result batch")
	seen = set()
	for item in items:
		if (
			not isinstance(item, dict)
			or item.get("event_id") not in expected
			or item["event_id"] in seen
			or item.get("payload_digest") != expected[item["event_id"]]["payload_digest"]
			or item.get("status") not in RESULTS
			or not isinstance(item.get("capture_event"), str)
			or not 1 <= len(item["capture_event"]) <= 140
		):
			frappe.throw("Receiver result does not match the requested event")
		seen.add(item["event_id"])
	for item in items:
		# Merge only reporting fields while locked, preserving concurrent receipt/ignore writes.
		event = frappe.get_doc("Dispatch Event", item["event_id"], for_update=True)
		destination = next(
			r for r in event.instances if r.instance_type == peer.doctype and r.instance_name == peer.name
		)
		with system_write():
			destination.capture_event = item["capture_event"]
			destination.receiver_status = item["status"]
			destination.receiver_exception = str(item.get("exception") or "")[:2000]
			destination.receiver_retries = frappe.utils.cint(item.get("retries"))
			destination.receiver_next_attempt_at = item.get("next_attempt_at")
			destination.status_checked_at = frappe.utils.now_datetime()
			event.save(ignore_permissions=True)


def migrate_delivery_labels():
	"""Old Synced badges prove receipt only; receiver results are fetched separately."""
	frappe.db.sql("UPDATE `tabDispatch Event` SET status='Delivered' WHERE status='Synced'")
	frappe.db.sql(
		"UPDATE `tabDispatch Event` SET status='Partially Delivered' WHERE status='Partially Synced'"
	)
