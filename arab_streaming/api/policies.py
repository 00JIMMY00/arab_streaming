import json

import frappe

from arab_streaming.api.events import addressed
from arab_streaming.protocol.codec import digest
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.policy import RULE_FIELDS, stored_rules


def bundle(peer):
	rows = [dict(r) for r in stored_rules() if r.all_branches or peer.restaurant_branch in r.branches]
	for r in rows:
		# Only the published rule contract, never owners/credentials/other branches.
		for key in list(r):
			if key not in (*RULE_FIELDS, "name", "branches", "field_mappings", "configuration_hash"):
				r.pop(key, None)
		r["branches"] = [peer.restaurant_branch]
		r["all_branches"] = 0
	return {
		"rules": rows,
		"policy_hash": digest(rows),
		"branch": peer.restaurant_branch,
		"capabilities": ["dynamic_rules", "branch_scope", "lifecycle", "peer_push", "files"],
	}


def install(peer, state, data):
	from arab_streaming.streaming.permissions import provision
	from arab_streaming.streaming.policy import ensure_branch_field

	if not isinstance(data, dict) or data.get("branch") != peer.restaurant_branch:
		frappe.throw("Policy Branch does not match this pairing", frappe.PermissionError)
	# Push, pull and the operator's configuration check can publish concurrently.
	# Serialize installation before loading/saving the individual rule documents.
	state = frappe.get_doc("Streaming Peer State", state.name, for_update=True)
	rules = data.get("rules")
	if not isinstance(rules, list) or len(rules) > 1000 or data.get("policy_hash") != digest(rules):
		frappe.throw("Invalid policy bundle")
	if not frappe.db.exists("Branch", peer.restaurant_branch):
		frappe.get_doc(dict(doctype="Branch", branch=peer.restaurant_branch)).insert(ignore_permissions=True)
	existing = set(frappe.get_all("Streaming Rule", pluck="name"))
	incoming = set()
	errors = []
	valid_rules = []
	with system_write():
		for row in rules:
			if not isinstance(row, dict) or not isinstance(row.get("name"), str):
				frappe.throw("Invalid rule identity")
			from arab_streaming.streaming.adapters import is_business_doctype

			if not is_business_doctype(row.get("doctype_name")):
				errors.append(
					dict(
						doctype=row.get("doctype_name"),
						error="Install this business DocType and its controller on the branch, then Test Configuration on the master",
					)
				)
				continue
			valid_rules.append(row)
			if row.get("scope") == "Branch" and row.get("enabled"):
				field = ensure_branch_field(row["doctype_name"])
				if not row.get("branch_field"):
					row["branch_field"] = field
			name = row["name"]
			incoming.add(name)
			doc = (
				frappe.get_doc("Streaming Rule", name)
				if name in existing
				else frappe.get_doc(dict(doctype="Streaming Rule", name=name))
			)
			for key in RULE_FIELDS:
				doc.set(key, row.get(key))
			doc.branches = []
			doc.append("branches", {"branch": peer.restaurant_branch})
			doc.all_branches = 0
			doc.field_mappings = []
			for item in row.get("field_mappings") or []:
				doc.append("field_mappings", item)
			if name not in existing:
				doc.insert(ignore_permissions=True, set_name=name)
			else:
				doc.save(ignore_permissions=True)
		for name in existing - incoming:
			frappe.db.set_value("Streaming Rule", name, "enabled", 0)
		state.policy_hash = data["policy_hash"]
		state.policy_errors = json.dumps(errors) if errors else None
		frappe.db.set_value(
			state.doctype,
			state.name,
			{"policy_hash": state.policy_hash, "policy_errors": state.policy_errors},
		)
	provision(state.execution_user, valid_rules)
	return {"policy_hash": state.policy_hash, "rules": len(valid_rules), "errors": errors}


@frappe.whitelist(methods=["POST"])
def published(protocol, version, pairing_id, source_installation_id, pairing_generation):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	if frappe.conf.arab_streaming_site_role != "Master":
		frappe.throw("Policies are owned by the master")
	return bundle(peer)


@frappe.whitelist(methods=["POST"])
def accept(protocol, version, pairing_id, source_installation_id, pairing_generation, policy):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	if frappe.conf.arab_streaming_site_role != "Slave":
		frappe.throw("Only branch sites receive published policies")
	return install(peer, state, policy)


@frappe.whitelist(methods=["POST"])
def register_receiver(
	protocol, version, pairing_id, source_installation_id, pairing_generation, access_key, secret
):
	import re

	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	if frappe.conf.arab_streaming_site_role != "Master":
		frappe.throw("Register the branch receiver on its master")
	if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9]{15}", x) for x in (access_key, secret)):
		frappe.throw("Invalid paired receiver credentials")
	with system_write():
		state.remote_access_key = access_key
		state.remote_secret = secret
		state.save(ignore_permissions=True)
	return bundle(peer)
