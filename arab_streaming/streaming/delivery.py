"""Prompt, bounded per-peer delivery; the minute scheduler is recovery only."""

import json
import secrets
import string

import frappe

from arab_streaming.streaming.client import remote_call
from arab_streaming.streaming.connections import peer_state, wire_identity
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.transport import (
	acknowledge_for,
	exchange,
	pending_for,
	record_errors,
	source_head,
)


def token():
	return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(15))


def synchronize_policy(peer, force=False):
	state = peer_state(peer.doctype, peer.name)
	if peer.doctype == "Master Instance":
		from arab_streaming.api.policies import install

		if not state.incoming_access_key:
			with system_write():
				state.incoming_access_key = token()
				state.incoming_secret = token()
				state.save(ignore_permissions=True)
			frappe.db.commit()
		if not state.receiver_registered:
			result = remote_call(
				peer,
				"arab_streaming.api.policies.register_receiver",
				**wire_identity(peer),
				access_key=state.incoming_access_key,
				secret=state.get_password("incoming_secret"),
			)
			with system_write():
				state.receiver_registered = 1
				state.save(ignore_permissions=True)
		else:
			result = remote_call(peer, "arab_streaming.api.policies.published", **wire_identity(peer))
		if force or result["policy_hash"] != state.policy_hash:
			install(peer, state, result)
		frappe.db.commit()
	else:
		if not state.remote_access_key:
			return False
		from arab_streaming.api.policies import bundle

		policy = bundle(peer)
		if force or policy["policy_hash"] != state.policy_hash:
			result = remote_call(
				peer, "arab_streaming.api.policies.accept", **wire_identity(peer), policy=policy
			)
			if result.get("policy_hash") != policy["policy_hash"]:
				frappe.throw("Receiver did not accept the published policy")
			with system_write():
				frappe.db.set_value(
					state.doctype,
					state.name,
					{
						"policy_hash": policy["policy_hash"],
						"policy_errors": json.dumps(result["errors"]) if result.get("errors") else None,
					},
				)
			frappe.db.commit()
	return True


def process(instance_type, name):
	peer = frappe.get_doc(instance_type, name)
	if not peer.enabled or peer.connection_status != "Connected":
		return
	if not synchronize_policy(peer):
		return
	if instance_type == "Master Instance":
		exchange(peer)
	else:
		for _ in range(10):
			outgoing = pending_for(peer)
			if not outgoing:
				break
			result = remote_call(
				peer,
				"arab_streaming.api.events.receive",
				**wire_identity(peer),
				envelopes=outgoing,
				source_head=source_head(),
			)
			acknowledge_for(peer, result["receipts"])
			record_errors(peer, result.get("errors") or [])
			frappe.db.commit()
	from arab_streaming.streaming.executor import execute_captures

	execute_captures(only_peer=peer.name)
	from arab_streaming.streaming.feedback import refresh_results

	refresh_results(peer)
	from arab_streaming.streaming.stock import reconcile

	if frappe.conf.get("arab_streaming_advanced_stock_checks"):
		reconcile(peer)
	with system_write():
		state = peer_state(peer.doctype, peer.name)
		frappe.db.set_value(
			state.doctype,
			state.name,
			{
				"last_delivery": frappe.utils.now_datetime(),
				"agent_heartbeat": frappe.utils.now_datetime(),
				"last_delivery_error": None,
			},
		)
	frappe.db.commit()


def run_peer(instance_type, name):
	lock = frappe.cache.lock(
		f"arab-streaming-peer:{frappe.local.site}:{instance_type}:{name}", timeout=300, blocking=False
	)
	if not lock.acquire(blocking=False):
		return
	try:
		try:
			process(instance_type, name)
		except Exception as exc:
			frappe.db.rollback()
			state = peer_state(instance_type, name)
			frappe.db.set_value(
				"Streaming Peer State",
				state.name,
				{"last_delivery_error": str(exc)[:1500], "agent_heartbeat": frappe.utils.now_datetime()},
			)
			frappe.db.commit()
	finally:
		lock.release()
