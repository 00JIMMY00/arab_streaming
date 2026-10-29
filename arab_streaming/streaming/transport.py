import json
from datetime import timezone
from zoneinfo import ZoneInfo

import frappe

from arab_streaming.protocol.envelope import canonical_json, payload_digest, validate_envelope
from arab_streaming.protocol.version import PROTOCOL, VERSION
from arab_streaming.streaming.client import remote_call
from arab_streaming.streaming.connections import peer_state, site_id, wire_identity
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.inbox import ingest
from arab_streaming.streaming.status import dispatch_status


def envelope(event, destination):
	created = frappe.utils.get_datetime(event.created_on)
	if not created.tzinfo:
		created = created.replace(tzinfo=ZoneInfo(frappe.utils.get_system_timezone()))
	result = dict(
		protocol=PROTOCOL,
		version=VERSION,
		source_installation_id=site_id(),
		pairing_id=destination.pairing_id,
		pairing_generation=str(destination.pairing_generation),
		event_id=event.name,
		event_sequence=str(event.event_sequence),
		created_on=created.astimezone(timezone.utc).isoformat(),
		created_by=event.created_by,
		reference_doctype=event.reference_doctype,
		reference_name=event.reference_name,
		event_type=event.event_type,
		document_event=event.document_event,
		data=frappe.parse_json(event.data),
		metadata=frappe.parse_json(event.metadata) if event.metadata else {},
		doctype_config=frappe.parse_json(destination.doctype_config),
	)
	result["payload_digest"] = payload_digest(result)
	validate_envelope(result)
	return result


def pending_for(peer, limit=25):
	rows = frappe.db.sql(
		"""SELECT child.name, child.parent FROM `tabDispatch Event Instance` child
		JOIN `tabDispatch Event` event ON event.name=child.parent
		WHERE child.instance_type=%s AND child.instance_name=%s AND child.synced=0 AND child.`ignore`=0
		AND (child.next_attempt_at IS NULL OR child.next_attempt_at<=%s)
		ORDER BY event.event_sequence LIMIT %s""",
		(peer.doctype, peer.name, frappe.utils.now_datetime(), min(max(int(limit), 1), 50)),
		as_dict=True,
	)
	items, size = [], 0
	state = peer_state(peer.doctype, peer.name)
	for row in rows:
		event = frappe.get_doc("Dispatch Event", row.parent)
		destination = next(r for r in event.instances if r.name == row.name)
		if (
			destination.pairing_id != state.pairing_id
			or destination.pairing_generation != state.pairing_generation
		):
			frappe.throw("Historical destination belongs to a different pairing")
		item = envelope(event, destination)
		size += len(canonical_json(item))
		if size > 3_500_000:
			break
		items.append(item)
	return items


def acknowledge_for(peer, receipts):
	if not isinstance(receipts, list) or len(receipts) > 50:
		frappe.throw("Invalid receipt batch")
	with system_write():
		state = peer_state(peer.doctype, peer.name)
		for receipt in receipts:
			if not isinstance(receipt, dict):
				frappe.throw("Invalid event receipt")
			event = frappe.get_doc("Dispatch Event", receipt.get("event_id"), for_update=True)
			destination = next(
				(
					row
					for row in event.instances
					if row.instance_type == peer.doctype and row.instance_name == peer.name
				),
				None,
			)
			if not destination or destination.ignore:
				frappe.throw("Receipt is not addressed to this peer", frappe.PermissionError)
			if receipt.get("payload_digest") != envelope(event, destination)[
				"payload_digest"
			] or not receipt.get("capture_event"):
				frappe.throw("Receipt digest does not match its dispatch")
			destination.capture_event = receipt["capture_event"]
			if not destination.receiver_status:
				destination.receiver_status = "Pending"
			destination.synced = 1
			destination.exception = None
			destination.next_attempt_at = None
			event.save(ignore_permissions=True)
			state.acknowledged_through = max(state.acknowledged_through or 0, int(event.event_sequence))
		state.save(ignore_permissions=True)


def exchange(connection):
	if not connection.enabled or connection.connection_status != "Connected":
		return
	identity = wire_identity(connection)
	state = peer_state("Master Instance", connection.name)
	try:
		outgoing = pending_for(connection)
		if outgoing:
			result = remote_call(
				connection,
				"arab_streaming.api.events.receive",
				**identity,
				envelopes=outgoing,
				source_head=source_head(),
			)
			acknowledge_for(connection, result["receipts"])
			record_errors(connection, result.get("errors") or [])
			frappe.db.commit()
		frappe.db.set_value("Master Instance", connection.name, "last_dispatch_exception", None)
	except Exception as exc:
		frappe.db.rollback()
		frappe.db.set_value("Master Instance", connection.name, "last_dispatch_exception", str(exc)[:1000])
		frappe.db.commit()
	try:
		result = remote_call(connection, "arab_streaming.api.events.pending", **identity, limit=25)
		incoming = result["envelopes"]
		if incoming:
			receipts = ingest(connection, state, incoming, source_head=result.get("source_head"))
			frappe.db.commit()  # Durable local receipt must precede the remote acknowledgement.
			remote_call(connection, "arab_streaming.api.events.acknowledge", **identity, receipts=receipts)
		frappe.db.set_value("Master Instance", connection.name, "last_capture_exception", None)
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		frappe.db.set_value("Master Instance", connection.name, "last_capture_exception", str(exc)[:1000])
		frappe.db.commit()


def source_head():
	from arab_streaming.streaming.policy import committed

	with committed() as cursor:
		cursor.execute("SELECT event_sequence FROM `tabStreaming Site State` WHERE name='local'")
		return str(cursor.fetchone()["event_sequence"])


def record_errors(peer, errors):
	for item in errors:
		name = frappe.db.get_value(
			"Dispatch Event Instance",
			{"parent": item.get("event_id"), "instance_type": peer.doctype, "instance_name": peer.name},
		)
		if not name:
			continue
		event = frappe.get_doc("Dispatch Event", item["event_id"], for_update=True)
		row = next(row for row in event.instances if row.name == name)
		if row.synced or row.ignore:
			continue
		with system_write():
			row.attempts = (row.attempts or 0) + 1
			row.exception = str(item.get("error") or "Delivery rejected")[:1500]
			row.next_attempt_at = frappe.utils.add_to_date(
				frappe.utils.now_datetime(), seconds=min(60, 2 ** min(row.attempts, 6))
			)
			event.save(ignore_permissions=True)
