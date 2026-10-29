import json
import re
import uuid

import frappe

from arab_streaming.auth import require_wire_auth
from arab_streaming.protocol.version import PROTOCOL, VERSION
from arab_streaming.streaming.configuration import require_role
from arab_streaming.streaming.connections import (
	bound_peer,
	local_facts,
	site_id,
	validate_facts,
	validate_version,
)
from arab_streaming.streaming.guards import system_write


def authorize(protocol, version):
	key = require_wire_auth()
	require_role("Master")
	validate_version(protocol, version)
	return key


@frappe.whitelist(methods=["POST"])
def capabilities(protocol, version):
	authorize(protocol, version)
	return dict(
		protocol=PROTOCOL,
		version=VERSION,
		role="Master",
		site_id=site_id(),
		capabilities=["dynamic_rules", "branch_scope", "lifecycle", "peer_push", "files"],
		metadata_driven=True,
	)


@frappe.whitelist(methods=["POST"])
def branches(protocol, version):
	authorize(protocol, version)
	assigned = frappe.get_all(
		"Streaming Peer State", filters={"active_branch": ["is", "set"]}, pluck="active_branch"
	)
	rows = frappe.get_list(
		"Branch", filters={"custom_streaming_enabled": 1}, fields=["name"], limit_page_length=100
	)
	return {"branches": [row.name for row in rows if row.name not in assigned]}


@frappe.whitelist(methods=["POST"])
def link(protocol, version, branch, request_id, facts):
	key = authorize(protocol, version)
	facts = validate_facts(facts, "Slave")
	if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9-]{36}", request_id):
		frappe.throw("Invalid link request identity")
	branch_doc = frappe.get_doc("Branch", branch, for_update=True)
	branch_doc.check_permission("read")
	if not branch_doc.custom_streaming_enabled or facts.get("branch") != branch:
		frappe.throw("This branch is not enabled for streaming")
	state_name = frappe.db.get_value("Streaming Peer State", {"link_request_id": request_id})
	with system_write():
		if state_name:
			state = frappe.get_doc("Streaming Peer State", state_name, for_update=True)
			peer = frappe.get_doc("Slave Instance", state.instance_name)
			if (
				state.remote_installation_id != facts["site_id"]
				or peer.restaurant_branch != branch
				or peer.api_key != key.api_key
				or not peer.enabled
			):
				frappe.throw(
					"The link request does not match its original authorized peer", frappe.PermissionError
				)
		else:
			if frappe.db.exists("Streaming Peer State", {"active_branch": branch}):
				frappe.throw("This branch is already assigned to another streaming peer")
			peer = frappe.get_doc(
				dict(
					doctype="Slave Instance",
					restaurant_branch=branch,
					api_key=key.api_key,
					connection_status="Disconnected",
					enabled=1,
				)
			)
			peer.insert(ignore_permissions=True)
			state = frappe.get_doc(
				dict(
					doctype="Streaming Peer State",
					instance_type="Slave Instance",
					instance_name=peer.name,
					remote_installation_id=facts["site_id"],
					pairing_id=str(uuid.uuid4()),
					pairing_generation=1,
					active_branch=branch,
					link_request_id=request_id,
					execution_user=key.user,
				)
			)
			state.insert(ignore_permissions=True)
		peer.connection_status = "Connected"
		peer.domain = facts["domain"]
		peer.streaming_version = VERSION
		peer.receiving_doctypes_data = json.dumps(facts, ensure_ascii=False)
		peer.save(ignore_permissions=True)
	return {
		"instance_name": peer.name,
		"pairing_id": state.pairing_id,
		"pairing_generation": str(state.pairing_generation),
		"facts": local_facts(peer),
	}


@frappe.whitelist(methods=["POST"])
def ping(protocol, version, pairing_id, source_installation_id, pairing_generation, facts):
	key = authorize(protocol, version)
	peer, _state = bound_peer(key, pairing_id, source_installation_id, pairing_generation, connected=False)
	facts = validate_facts(facts, "Slave")
	if facts["site_id"] != source_installation_id or facts.get("branch") != peer.restaurant_branch:
		frappe.throw("Facts do not match the linked source", frappe.PermissionError)
	with system_write():
		peer.domain = facts["domain"]
		peer.connection_status = "Connected"
		peer.receiving_doctypes_data = json.dumps(facts, ensure_ascii=False)
		peer.save(ignore_permissions=True)
	return {"facts": local_facts(peer)}


@frappe.whitelist(methods=["POST"])
def disconnect(protocol, version, pairing_id, source_installation_id, pairing_generation):
	peer, _ = bound_peer(
		authorize(protocol, version), pairing_id, source_installation_id, pairing_generation, connected=False
	)
	with system_write():
		peer.connection_status = "Disconnected"
		peer.save(ignore_permissions=True)
	return {"status": "Disconnected"}
