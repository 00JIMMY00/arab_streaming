"""Repeatable, bounded copies of existing records through the normal event pipeline."""

import json
import time

import frappe

from arab_streaming.streaming.connections import peer_state
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.policy import RULE_FIELDS, config_for, matches_scope

ACTIVE = ("Queued", "Copying", "Waiting", "Needs Attention", "Failed")
DIRECTIONS = ("Master to Slave", "Slave to Master")


def source_rule(peer, rule_name):
	if (
		not frappe.conf.get("arab_streaming_enabled")
		or not peer.enabled
		or peer.connection_status != "Connected"
	):
		frappe.throw("Enable and connect both sites before copying existing records")
	rule = frappe.get_doc("Streaming Rule", rule_name)
	config = config_for(peer, rule.doctype_name)
	if not config or config.name != rule.name:
		frappe.throw(
			"This rule does not allow copying from this source to this Branch", frappe.PermissionError
		)
	return config


def selection(config, peer, before=None, after=None):
	from arab_streaming.streaming.exports import selection as rule_selection

	filters = rule_selection(config)
	if before:
		filters.append(["creation", "<=", before])
	if after:
		filters.append(["name", ">", after])
	field = config.get("branch_field") or ""
	direct = config.get("scope") == "Shared" or (
		config.get("scope") == "Branch" and "." not in field and bool(field)
	)
	if config.get("scope") == "Branch" and field and "." not in field:
		filters.append([field, "=", peer.restaurant_branch])
	return filters, direct


def preview_source(peer, rule_name):
	config = source_rule(peer, rule_name)
	state = peer_state(peer.doctype, peer.name)
	with replay(state.pairing_id, state.execution_user):
		filters, direct = selection(config, peer)
		if direct:
			count = frappe.get_list(config.doctype_name, filters=filters, fields=["count(name) as count"])[0][
				"count"
			]
			return preview_result(peer, config, count, False)
		# Nontrivial resolvers may need linked records. Bound preview work, not the copy itself.
		count, cursor, started = 0, None, time.monotonic()
		while True:
			filters, _ = selection(config, peer, after=cursor)
			names = frappe.get_list(
				config.doctype_name, filters=filters, pluck="name", order_by="name", limit_page_length=100
			)
			for name in names:
				doc = frappe.get_doc(config.doctype_name, name)
				if matches_scope(doc, config, peer.restaurant_branch):
					count += 1
			if len(names) < 100:
				return preview_result(peer, config, count, False)
			cursor = names[-1]
			if time.monotonic() - started > 5:
				return preview_result(peer, config, count, True)


def preview_result(peer, config, count, at_least):
	return dict(
		count=count,
		at_least=at_least,
		rule_revision=config.revision,
		recent_runs=frappe.get_all(
			"Streaming Copy Run",
			filters={"rule": config.name, "instance_type": peer.doctype, "instance_name": peer.name},
			fields=["name", "status"],
			order_by="creation desc",
			limit_page_length=5,
		),
	)


def start_source(peer, rule_name, request_id, existing_policy="Skip", revision=None):
	import re

	if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", request_id):
		frappe.throw("Invalid copy request identity")
	if existing_policy not in ("Skip", "Rule"):
		frappe.throw("Choose Preserve Existing or Follow Rule")
	config = source_rule(peer, rule_name)
	if str(config.revision) != str(revision):
		frappe.throw("The rule changed; preview the copy again before starting")
	state = peer_state(peer.doctype, peer.name, lock=True)
	previous = frappe.db.get_value("Streaming Copy Run", {"request_id": request_id})
	if previous:
		run = authorized_run(peer, previous)
		if run.rule != rule_name or run.existing_policy != existing_policy:
			frappe.throw("An existing copy request cannot change its scope or policy")
		return summary(run)
	busy = frappe.db.get_value(
		"Streaming Copy Run",
		{
			"instance_type": peer.doctype,
			"instance_name": peer.name,
			"rule": rule_name,
			"status": ["in", ACTIVE],
		},
	)
	if busy:
		frappe.throw(
			"Copy run " + busy + " is unfinished. Review, resume or stop it before starting another."
		)
	configuration = {key: config.get(key) for key in RULE_FIELDS}
	configuration.update(
		rule_id=config.name, rule_revision=config.revision, field_mappings=config.get("field_mappings") or []
	)
	with system_write():
		run = frappe.get_doc(
			dict(
				doctype="Streaming Copy Run",
				rule=rule_name,
				reference_doctype=config.doctype_name,
				direction="Master to Slave"
				if frappe.conf.arab_streaming_site_role == "Master"
				else "Slave to Master",
				branch=peer.restaurant_branch,
				instance_type=peer.doctype,
				instance_name=peer.name,
				pairing_id=state.pairing_id,
				pairing_generation=state.pairing_generation,
				request_id=request_id,
				existing_policy=existing_policy,
				requested_by=frappe.session.user,
				configuration=json.dumps(configuration),
				started_at=frappe.utils.now_datetime(),
				source_url=frappe.conf.get("arab_streaming_public_url") or frappe.utils.get_url(),
				destination_url=peer.domain,
			)
		).insert(ignore_permissions=True)
	from arab_streaming.tasks import wakeup

	frappe.db.after_commit.add(wakeup)
	return summary(run)


def authorized_run(peer, name, lock=False):
	run = frappe.get_doc("Streaming Copy Run", name, for_update=lock)
	state = peer_state(peer.doctype, peer.name)
	if (
		run.instance_type != peer.doctype
		or run.instance_name != peer.name
		or run.pairing_id != state.pairing_id
		or run.pairing_generation != state.pairing_generation
	):
		frappe.throw("Copy run does not belong to this pairing", frappe.PermissionError)
	return run


def summary(run):
	return {
		key: run.get(key)
		for key in (
			"name",
			"status",
			"rule",
			"reference_doctype",
			"direction",
			"branch",
			"existing_policy",
			"source_url",
			"destination_url",
			"queued_records",
			"applied_records",
			"skipped_records",
			"failed_records",
			"pending_records",
			"scanned_records",
			"scan_complete",
			"exception",
		)
	}


def control_source(peer, name, action):
	run = authorized_run(peer, name, lock=True)
	if action == "stop":
		if run.status not in ACTIVE:
			frappe.throw("This copy run is already finished")
		run.status = "Stopped"
		run.finished_at = frappe.utils.now_datetime()
	elif action == "resume":
		if run.status != "Failed":
			frappe.throw("Resume is available only for a failed source scan")
		validate_run(peer, run)
		run.status, run.exception = "Queued", None
	else:
		frappe.throw("Unknown copy action")
	with system_write():
		run.save(ignore_permissions=True)
	return summary(run)


def validate_run(peer, run):
	config = source_rule(peer, run.rule)
	if str(config.revision) != str(frappe.parse_json(run.configuration)["rule_revision"]):
		frappe.throw("Rule changed during this copy. Stop this run and start a new preview.")
	state = peer_state(peer.doctype, peer.name)
	if run.pairing_id != state.pairing_id or run.pairing_generation != state.pairing_generation:
		frappe.throw("Pairing changed; review this run before copying more records")
	return config, state


def update_counts(run):
	counts = dict(
		frappe.db.sql(
			"SELECT status, count(*) FROM `tabDispatch Event` WHERE copy_run=%s GROUP BY status", (run.name,)
		)
	)
	run.applied_records = counts.get("Applied", 0)
	run.skipped_records = counts.get("Skipped", 0) + counts.get("Completed with Skips", 0)
	run.failed_records = counts.get("Failed", 0) + counts.get("Delivery Failed", 0) + counts.get("Ignored", 0)
	run.pending_records = run.queued_records - run.applied_records - run.skipped_records - run.failed_records
	if run.scan_complete and run.status not in ("Stopped", "Failed"):
		run.status = (
			"Needs Attention" if run.failed_records else "Waiting" if run.pending_records else "Complete"
		)
		if run.status == "Complete":
			run.finished_at = frappe.utils.now_datetime()
	with system_write():
		run.save(ignore_permissions=True)


def step(peer, run_name):
	"""Event and cursor commit together, so retrying a source batch cannot duplicate it."""
	run = authorized_run(peer, run_name, lock=True)
	if run.status not in ("Queued", "Copying"):
		update_counts(run)
		frappe.db.commit()
		return False
	config, state = validate_run(peer, run)
	with replay(state.pairing_id, state.execution_user):
		# Use the same fence as business saves; take the snapshot after it is acquired.
		frappe.db.sql("SELECT event_sequence FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE")
		filters, _ = selection(config, peer, run.started_at, run.last_name)
		names = frappe.get_list(
			config.doctype_name, filters=filters, pluck="name", order_by="name", limit_page_length=1
		)
		if not names:
			run.scan_complete = 1
			run.status = "Waiting"
		else:
			name = names[0]
			doc = frappe.get_doc(config.doctype_name, name, for_update=True)
			doc.check_permission("read")
			if matches_scope(doc, config, peer.restaurant_branch):
				# Never add a second operation behind an existing unresolved operation for this record.
				unresolved = frappe.db.sql(
					"""SELECT event.name FROM `tabDispatch Event` event
				JOIN `tabDispatch Event Instance` dest ON dest.parent=event.name
				WHERE event.reference_doctype=%s AND event.reference_name=%s
				AND dest.instance_type=%s AND dest.instance_name=%s AND dest.`ignore`=0
				AND (dest.synced=0 OR dest.receiver_status IN ('Pending','Failed'))
				ORDER BY event.event_sequence LIMIT 1""",
					(doc.doctype, doc.name, peer.doctype, peer.name),
				)
				if unresolved:
					frappe.throw(
						"Resolve existing Dispatch Event "
						+ unresolved[0][0]
						+ " for this record, then Resume this copy."
					)
				from arab_streaming.streaming.outbox import store_event

				captured = frappe.parse_json(run.configuration)
				store_event(
					doc,
					"Create",
					[(peer, state, captured)],
					copy_run=run.name,
					preserve_existing=run.existing_policy == "Skip",
				)
				run.queued_records += 1
			run.last_name = name
			run.scanned_records += 1
			run.status = "Copying"
		update_counts(run)
	frappe.db.commit()
	return bool(names)


def advance(peer, maximum=5):
	"""A small source batch per existing peer-agent pass; no new daemon or bulk payload."""
	names = frappe.get_all(
		"Streaming Copy Run",
		filters={"instance_type": peer.doctype, "instance_name": peer.name, "status": ["in", ACTIVE]},
		pluck="name",
		order_by="modified",
		limit_page_length=10,
	)
	started = time.monotonic()
	remaining = maximum
	for name in names:
		try:
			for _ in range(remaining):
				remaining -= 1
				if not step(peer, name) or time.monotonic() - started > 1:
					break
		except Exception as exc:
			frappe.db.rollback()
			run = frappe.get_doc("Streaming Copy Run", name, for_update=True)
			if run.status != "Stopped":
				with system_write():
					run.status = "Failed"
					run.exception = str(exc)[:2000]
					run.save(ignore_permissions=True)
			frappe.db.commit()
		if remaining <= 0 or time.monotonic() - started > 1:
			break
