"""Checkpoint native companion cancellation against its original addressed event."""

import frappe

from arab_streaming.streaming.context import replay_source


def cancel(target):
	"""Use only inside a native owning document's authenticated cancellation.

	The native owner has already stored docstatus=2, removing its active backlink.
	The companion's original Cancel event and before-state remain authoritative;
	no link, permission, stock or accounting validation is bypassed.
	"""
	pairing = replay_source()
	name = frappe.db.get_value("Streaming Peer State", {"pairing_id": pairing}) if pairing else None
	if not name:
		frappe.throw("Companion cancellation requires an authenticated pairing", frappe.PermissionError)
	state = frappe.get_doc("Streaming Peer State", name)
	if state.execution_user != frappe.session.user:
		frappe.throw("Companion cancellation requires the paired execution identity", frappe.PermissionError)
	peer = frappe.get_doc(state.instance_type, state.instance_name)
	remote_name = (
		frappe.db.get_value(
			"Streaming Value Mapping",
			{
				"instance_type": peer.doctype,
				"instance_name": peer.name,
				"reference_doctype": target.doctype,
				"local_name": target.name,
			},
			"remote_name",
		)
		or target.name
	)
	capture_name = frappe.db.get_value(
		"Capture Event",
		{
			"instance_type": peer.doctype,
			"instance_name": peer.name,
			"pairing_id": state.pairing_id,
			"pairing_generation": state.pairing_generation,
			"reference_doctype": target.doctype,
			"reference_name": remote_name,
			"event_type": "Cancel",
			"status": ["in", ["Pending", "Failed"]],
			"ignore": 0,
		},
		order_by="event_sequence asc",
	)
	if not capture_name:
		frappe.throw("Wait for the companion's original addressed Cancel event")
	capture = frappe.get_doc("Capture Event", capture_name)
	data = frappe.parse_json(capture.data)
	document = data.get("document") or {}
	config = frappe.parse_json(capture.doctype_config)
	base = config.get("expected_remote_base") or data.get("base_digest")
	if (
		document.get("doctype") != target.doctype
		or document.get("name") != remote_name
		or document.get("docstatus") != 2
		or not base
	):
		frappe.throw("A companion requires its exact cancelled source snapshot and original base")
	from arab_streaming.streaming.mapping import check_base, remember
	from arab_streaming.streaming.policy import authorize_document

	authorize_document(peer, document, incoming=True)
	target.check_permission("read")
	target.check_permission("cancel")
	check_base(peer, document, target, base)
	target.cancel()
	remember(peer, document, target)
	return target
