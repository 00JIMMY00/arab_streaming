"""Operator copy controls and narrowly paired source-side requests."""

import frappe

from arab_streaming.api.events import addressed
from arab_streaming.streaming import copying
from arab_streaming.streaming.client import remote_call
from arab_streaming.streaming.connections import wire_identity


def operator_rule(name):
	frappe.only_for("System Manager")
	rule = frappe.get_doc("Streaming Rule", name)
	rule.check_permission("read")
	if not rule.enabled:
		frappe.throw("Enable the rule before copying existing records")
	return rule


def eligible_peers(rule):
	doctype = "Slave Instance" if frappe.conf.arab_streaming_site_role == "Master" else "Master Instance"
	result = []
	for name in frappe.get_all(
		doctype, filters={"enabled": 1, "connection_status": "Connected"}, pluck="name"
	):
		peer = frappe.get_doc(doctype, name)
		if rule.all_branches or peer.restaurant_branch in [row.branch for row in rule.branches]:
			peer.check_permission("read")
			result.append(peer)
	return result


def resolve(name, direction, branch):
	rule = operator_rule(name)
	if direction not in copying.DIRECTIONS or rule.direction not in ("Both", direction):
		frappe.throw("This direction is not enabled by the rule", frappe.PermissionError)
	if frappe.conf.arab_streaming_site_role == "Slave" and direction != "Slave to Master":
		frappe.throw("Start master-source copies from the rule on the master", frappe.PermissionError)
	peers = [peer for peer in eligible_peers(rule) if peer.restaurant_branch == branch]
	if len(peers) != 1:
		frappe.throw("Choose one enabled, connected Branch allowed by this rule", frappe.PermissionError)
	return rule, peers[0]


def at_source(rule, peer, direction, action, **values):
	if frappe.conf.arab_streaming_site_role == "Master" and direction == "Slave to Master":
		from arab_streaming.streaming.delivery import synchronize_policy

		if action in ("preview", "start"):
			synchronize_policy(peer, force=True)
		return remote_call(
			peer,
			"arab_streaming.api.copy.source_" + action,
			**wire_identity(peer),
			rule_name=rule.name,
			**values,
		)
	if action == "preview":
		return copying.preview_source(peer, rule.name)
	if action == "start":
		return copying.start_source(peer, rule.name, **values)
	run = copying.authorized_run(peer, values["run_name"])
	if run.rule != rule.name:
		frappe.throw("Copy run belongs to a different rule", frappe.PermissionError)
	if action == "progress":
		return copying.summary(run)
	return copying.control_source(peer, run.name, values["action"])


@frappe.whitelist()
def options(name):
	rule = operator_rule(name)
	directions = [d for d in copying.DIRECTIONS if rule.direction in ("Both", d)]
	if frappe.conf.arab_streaming_site_role == "Slave":
		directions = [d for d in directions if d == "Slave to Master"]
	return {
		"directions": directions,
		"branches": [p.restaurant_branch for p in eligible_peers(rule)],
		"duplicate_policy": rule.on_duplicated_insert or "Update",
	}


@frappe.whitelist(methods=["POST"])
def preview(name, direction, branch):
	rule, peer = resolve(name, direction, branch)
	result = at_source(rule, peer, direction, "preview")
	master = frappe.conf.get("arab_streaming_public_url") or frappe.utils.get_url()
	result.update(
		source_url=peer.domain
		if direction == "Slave to Master" and frappe.conf.arab_streaming_site_role == "Master"
		else master,
		destination_url=peer.domain,
		branch=branch,
		direction=direction,
	)
	# On the master, the reverse copy's destination is this site.
	if frappe.conf.arab_streaming_site_role == "Master" and direction == "Slave to Master":
		result["destination_url"] = master
	return result


@frappe.whitelist(methods=["POST"])
def start(name, direction, branch, request_id, existing_policy="Skip", revision=None):
	rule, peer = resolve(name, direction, branch)
	return at_source(
		rule,
		peer,
		direction,
		"start",
		request_id=request_id,
		existing_policy=existing_policy,
		revision=revision,
	)


@frappe.whitelist(methods=["POST"])
def progress(name, direction, branch, run_name):
	rule, peer = resolve(name, direction, branch)
	return at_source(rule, peer, direction, "progress", run_name=run_name)


@frappe.whitelist(methods=["POST"])
def control(name, direction, branch, run_name, action):
	rule, peer = resolve(name, direction, branch)
	return at_source(rule, peer, direction, "control", run_name=run_name, action=action)


@frappe.whitelist(methods=["POST"])
def control_run(name, action):
	frappe.only_for("System Manager")
	run = frappe.get_doc("Streaming Copy Run", name)
	run.check_permission("read")
	peer = frappe.get_doc(run.instance_type, run.instance_name)
	return copying.control_source(peer, run.name, action)


def paired_source(protocol, version, pairing_id, source_installation_id, pairing_generation):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	if frappe.conf.arab_streaming_site_role != "Slave" or peer.doctype != "Master Instance":
		frappe.throw("Only the paired master can request a branch-source copy", frappe.PermissionError)
	return peer, state


@frappe.whitelist(methods=["POST"])
def source_preview(protocol, version, pairing_id, source_installation_id, pairing_generation, rule_name):
	peer, _state = paired_source(protocol, version, pairing_id, source_installation_id, pairing_generation)
	return copying.preview_source(peer, rule_name)


@frappe.whitelist(methods=["POST"])
def source_start(
	protocol,
	version,
	pairing_id,
	source_installation_id,
	pairing_generation,
	rule_name,
	request_id,
	existing_policy="Skip",
	revision=None,
):
	peer, _state = paired_source(protocol, version, pairing_id, source_installation_id, pairing_generation)
	return copying.start_source(peer, rule_name, request_id, existing_policy, revision)


@frappe.whitelist(methods=["POST"])
def source_progress(
	protocol, version, pairing_id, source_installation_id, pairing_generation, rule_name, run_name
):
	peer, _state = paired_source(protocol, version, pairing_id, source_installation_id, pairing_generation)
	run = copying.authorized_run(peer, run_name)
	if run.rule != rule_name:
		frappe.throw("Copy run belongs to a different rule", frappe.PermissionError)
	return copying.summary(run)


@frappe.whitelist(methods=["POST"])
def source_control(
	protocol, version, pairing_id, source_installation_id, pairing_generation, rule_name, run_name, action
):
	peer, _state = paired_source(protocol, version, pairing_id, source_installation_id, pairing_generation)
	run = copying.authorized_run(peer, run_name)
	if run.rule != rule_name:
		frappe.throw("Copy run belongs to a different rule", frappe.PermissionError)
	return copying.control_source(peer, run_name, action)
