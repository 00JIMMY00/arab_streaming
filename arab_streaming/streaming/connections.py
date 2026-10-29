import json
import uuid

import frappe
from frappe.utils import now_datetime

from arab_streaming import __version__
from arab_streaming.protocol.version import PROTOCOL, VERSION
from arab_streaming.streaming.client import remote_call
from arab_streaming.streaming.configuration import require_role
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.validation import normalize_site_url


def site_id():
	return frappe.db.get_value("Streaming Site State", "local", "installation_id")


def validate_version(protocol, version):
	if protocol != PROTOCOL or version != VERSION:
		frappe.throw("Incompatible Arab-ERP Streaming protocol")


def peer_state(instance_type, instance_name, lock=False):
	name = frappe.db.get_value(
		"Streaming Peer State", {"instance_type": instance_type, "instance_name": instance_name}
	)
	if not name:
		frappe.throw("Link this connection before using Ping or Sync")
	return frappe.get_doc("Streaming Peer State", name, for_update=lock)


def local_facts(instance):
	return dict(
		protocol=PROTOCOL,
		version=VERSION,
		app_version=__version__,
		site_id=site_id(),
		role=frappe.conf.arab_streaming_site_role,
		domain=normalize_site_url(
			instance.site_domain
			if instance.doctype == "Master Instance"
			else frappe.conf.get("arab_streaming_public_url") or frappe.utils.get_url()
		),
		branch=instance.restaurant_branch,
		instance_name=instance.name,
		rules=[
			{
				key: row.get(key)
				for key in __import__("arab_streaming.streaming.policy", fromlist=["RULE_FIELDS"]).RULE_FIELDS
			}
			for row in __import__(
				"arab_streaming.streaming.policy", fromlist=["effective_rules"]
			).effective_rules(instance)
		],
	)


def validate_facts(facts, expected_role):
	if isinstance(facts, str):
		facts = frappe.parse_json(facts)
	if not isinstance(facts, dict):
		frappe.throw("Invalid streaming facts")
	validate_version(facts.get("protocol"), facts.get("version"))
	if facts.get("role") != expected_role or not isinstance(facts.get("site_id"), str):
		frappe.throw("Unexpected remote streaming role or site identity")
	if not isinstance(facts.get("rules"), list) or len(facts["rules"]) > 100:
		frappe.throw("Invalid remote streaming rules")
	for row in facts["rules"]:
		if not isinstance(row, dict) or not isinstance(row.get("doctype_name"), str):
			frappe.throw("Invalid remote streaming rule")
		from arab_streaming.streaming.adapters import condition_fields
		from arab_streaming.streaming.conditions import parse

		parse(row.get("conditions"), condition_fields(row["doctype_name"]))
		for flag in (
			"lock_documents",
			"allow_creation",
			"lock_documents_after_submit",
			"allow_amend",
			"allow_rename",
			"prevent_delete",
			"ignore_branch",
		):
			if row.get(flag) is not None and (
				not isinstance(row[flag], (int, bool)) or row[flag] not in (0, 1)
			):
				frappe.throw("Remote streaming flags must be boolean values")
	normalize_site_url(facts.get("domain"))
	return facts


def bound_peer(key, pairing_id, source_installation_id, pairing_generation="1", connected=True):
	state_name = frappe.db.get_value("Streaming Peer State", {"pairing_id": pairing_id})
	if not state_name:
		frappe.throw("Unknown streaming pairing", frappe.PermissionError)
	state = frappe.get_doc("Streaming Peer State", state_name)
	if (
		state.instance_type
		!= (
			"Slave Instance" if frappe.conf.get("arab_streaming_site_role") == "Master" else "Master Instance"
		)
		or state.remote_installation_id != source_installation_id
		or str(state.pairing_generation) != str(pairing_generation)
	):
		frappe.throw("Streaming source does not match this pairing", frappe.PermissionError)
	peer = frappe.get_doc(state.instance_type, state.instance_name)
	if (
		(
			(key.get("peer_state") != state.name)
			if key.get("peer_state")
			else (state.instance_type != "Slave Instance" or peer.api_key != key.api_key)
		)
		or not peer.enabled
		or (connected and peer.connection_status != "Connected")
	):
		frappe.throw("This credential is not authorized for the peer", frappe.PermissionError)
	return peer, state


def wire_identity(connection):
	state = peer_state(connection.doctype, connection.name)
	return dict(
		pairing_id=state.pairing_id,
		source_installation_id=site_id(),
		pairing_generation=str(state.pairing_generation),
	)


def master_connection(name):
	require_role("Slave")
	frappe.only_for("System Manager")
	doc = frappe.get_doc("Master Instance", name)
	doc.check_permission("write")
	return doc


def eligible_branches(name):
	doc = master_connection(name)
	return remote_call(doc, "arab_streaming.api.connection.branches")["branches"]


def save_connection(doc):
	with system_write():
		doc.save(ignore_permissions=True)


def link_connection(name, branch):
	doc = master_connection(name)
	if not doc.enabled:
		frappe.throw("Enable the Master Instance before linking")
	if doc.syncing_status == "In Progress":
		frappe.throw("Initial copy is in progress")
	if doc.slave_name and doc.restaurant_branch and branch != doc.restaurant_branch:
		frappe.throw("A linked connection cannot be moved to another branch")
	user = doc.execution_user or frappe.conf.get("arab_streaming_execution_user")
	if not user or user in ("Guest", "Administrator") or not frappe.db.get_value("User", user, "enabled"):
		frappe.throw("Select an enabled, non-Administrator Execution User before Link")
	pairing = str(uuid.uuid5(uuid.NAMESPACE_URL, site_id() + ":" + doc.name))
	doc.last_requested_connection = now_datetime()
	attempt = doc.last_requested_connection
	frappe.db.savepoint("streaming_link")
	try:
		facts = local_facts(doc)
		facts["branch"] = branch
		result = remote_call(
			doc, "arab_streaming.api.connection.link", branch=branch, request_id=pairing, facts=facts
		)
		remote = validate_facts(result["facts"], "Master")
		if (
			remote.get("branch") != branch
			or remote.get("instance_name") != result.get("instance_name")
			or not isinstance(result.get("pairing_id"), str)
			or result.get("pairing_generation") != "1"
		):
			frappe.throw("Link returned an inconsistent counterpart identity")
		if not frappe.db.exists("Branch", branch) and frappe.has_permission("Branch", "create"):
			frappe.get_doc(dict(doctype="Branch", branch=branch, custom_streaming_enabled=0)).insert()
		state_name = frappe.db.get_value(
			"Streaming Peer State", {"instance_type": "Master Instance", "instance_name": name}
		)
		with system_write():
			if state_name:
				state = frappe.get_doc("Streaming Peer State", state_name)
				if (
					state.pairing_id != result["pairing_id"]
					or state.remote_installation_id != remote["site_id"]
				):
					frappe.throw(
						"Remote identity changed; reconcile the existing pairing before reconnecting"
					)
			else:
				state = frappe.get_doc(
					dict(
						doctype="Streaming Peer State",
						instance_type="Master Instance",
						instance_name=name,
						remote_installation_id=remote["site_id"],
						pairing_id=result["pairing_id"],
						pairing_generation=int(result["pairing_generation"]),
						active_branch="owning-master",
						execution_user=user,
					)
				)
				state.insert(ignore_permissions=True)
			doc.restaurant_branch = branch
			doc.slave_name = result["instance_name"]
			doc.execution_user = user
			doc.connection_status = "Connected"
			doc.receiving_doctypes_data = json.dumps(remote, ensure_ascii=False)
			doc.last_successful_connection = now_datetime()
			doc.last_connection_exception = None
			doc.save(ignore_permissions=True)
		return {"status": "Connected", "branch": branch, "slave_name": doc.slave_name}
	except Exception as exc:
		frappe.db.rollback(save_point="streaming_link")
		frappe.db.set_value(
			"Master Instance",
			name,
			{"last_requested_connection": attempt, "last_connection_exception": str(exc)[:1000]},
		)
		# Preserve attempt telemetry even though the form action reports failure.
		frappe.db.commit()
		raise


def ping_connection(name):
	doc = master_connection(name)
	identity = wire_identity(doc)
	doc.last_requested_connection = now_datetime()
	attempt = doc.last_requested_connection
	frappe.db.savepoint("streaming_ping")
	try:
		result = remote_call(doc, "arab_streaming.api.connection.ping", **identity, facts=local_facts(doc))
		facts = validate_facts(result["facts"], "Master")
		state = peer_state("Master Instance", name)
		if facts["site_id"] != state.remote_installation_id or facts["instance_name"] != doc.slave_name:
			frappe.throw("Ping returned an unexpected counterpart")
		doc.receiving_doctypes_data = json.dumps(facts, ensure_ascii=False)
		doc.last_successful_connection = now_datetime()
		doc.last_connection_exception = None
		doc.connection_status = "Connected"
		save_connection(doc)
		return {"status": "Connected", "last_successful_connection": str(doc.last_successful_connection)}
	except Exception as exc:
		frappe.db.rollback(save_point="streaming_ping")
		frappe.db.set_value(
			"Master Instance",
			name,
			{"last_requested_connection": attempt, "last_connection_exception": str(exc)[:1000]},
		)
		frappe.db.commit()
		raise


def disconnect_connection(name, disable=False):
	doc = master_connection(name)
	error = None
	if doc.slave_name:
		try:
			remote_call(doc, "arab_streaming.api.connection.disconnect", **wire_identity(doc))
		except Exception:
			error = "Remote disconnect could not be confirmed; local connection is disconnected. Reconcile by Link when reachable."
	doc.connection_status = "Disconnected"
	if disable:
		doc.enabled = 0
	doc.last_connection_exception = error
	save_connection(doc)
	return {"status": "Disconnected", "warning": error}
