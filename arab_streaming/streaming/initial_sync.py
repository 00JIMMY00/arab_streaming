import json
import uuid

import frappe

from arab_streaming.protocol.codec import digest
from arab_streaming.streaming.client import remote_call
from arab_streaming.streaming.connections import master_connection, peer_state, wire_identity
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.executor import apply_document, execute_captures
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.transport import exchange

ROW_FIELDS = (
	"doctype_name",
	"conditions",
	"sync_from",
	"mandatory",
	"ignore_links",
	"skip_if_exists",
	"skip_links_if_exists",
	"ignore_branch",
	"scope",
	"branch_field",
	"rule_id",
	"rule_revision",
)


def configuration(doc):
	return [
		{
			"row_key": row.name,
			**{
				key: str(row.get(key)) if key == "sync_from" and row.get(key) else row.get(key)
				for key in ROW_FIELDS
			},
		}
		for row in doc.initial_sync_doctypes
	]


def counts(name):
	doc = master_connection(name)
	if doc.connection_status != "Connected" or doc.syncing_status == "In Progress":
		frappe.throw("Connect first and wait for any initial copy to finish before counting")
	result = remote_call(
		doc, "arab_streaming.api.bootstrap.count", **wire_identity(doc), configuration=configuration(doc)
	)
	total = 0
	with system_write():
		for item in result["counts"]:
			row = next(row for row in doc.initial_sync_doctypes if row.name == item["row_key"])
			row.document_count = item["count"]
			if not row.synced:
				total += item["count"]
		doc.total_syncing_document = total
		doc.save(ignore_permissions=True)
	return {"total": total}


def start(name, continuing=False, paired_peer=None):
	if not frappe.conf.get("arab_streaming_enabled"):
		frappe.throw("Enable the slave streaming engine before initial copy")
	doc = paired_peer or master_connection(name)
	from arab_streaming.streaming.permissions import provision
	from arab_streaming.streaming.policy import stored_rules

	provision(
		doc.execution_user,
		[dict(r) for r in stored_rules() if r.enabled]
		+ [dict(doctype_name=r.doctype_name, enabled=1) for r in doc.initial_sync_doctypes],
	)
	doc = frappe.get_doc("Master Instance", name, for_update=True)
	if doc.connection_status != "Connected" or not doc.enabled:
		frappe.throw("Connect and enable this Master Instance before Sync")
	if doc.syncing_status == "In Progress":
		frappe.throw("Initial copy is already in progress")
	with system_write():
		if continuing:
			if doc.syncing_status != "Failed":
				frappe.throw("Continue is available only after initial copy has failed")
			names = frappe.get_all(
				"Streaming Initial Sync Run",
				filters={"master_instance": name},
				pluck="name",
				order_by="generation desc",
				limit_page_length=1,
			)
			if not names:
				frappe.throw("There is no failed run to Continue")
			run = frappe.get_doc("Streaming Initial Sync Run", names[0], for_update=True)
			if frappe.parse_json(run.configuration) != configuration(doc):
				frappe.throw("Configuration changed; start a new initial copy instead of Continue")
			run.status = "In Progress"
		else:
			if doc.syncing_status == "Failed":
				frappe.throw("Use Continue for the failed run, or change its configuration to start over")
			previous = frappe.db.sql(
				"SELECT generation FROM `tabStreaming Initial Sync Run` WHERE master_instance=%s ORDER BY generation DESC LIMIT 1 FOR UPDATE",
				(name,),
			)
			run = frappe.get_doc(
				dict(
					doctype="Streaming Initial Sync Run",
					master_instance=name,
					generation=(int(previous[0][0]) if previous else 0) + 1,
					configuration=json.dumps(configuration(doc)),
					status="In Progress",
					retention_hold=1,
				)
			)
			for row in doc.initial_sync_doctypes:
				row.synced = 0
				row.document_count = 0
				run.append("checkpoints", {"row_key": row.name, "doctype_name": row.doctype_name})
			run.insert(ignore_permissions=True)
		run.lease_token = str(uuid.uuid4())
		run.save(ignore_permissions=True)
		doc.syncing_status = "In Progress"
		doc.progress = 0
		doc.exceptions = None
		doc.save(ignore_permissions=True)
	frappe.enqueue(
		"arab_streaming.streaming.initial_sync.run_initial",
		name=name,
		run_name=run.name,
		token=run.lease_token,
		timeout=900,
		job_id="streaming-initial-" + run.name + "-" + run.lease_token,
		enqueue_after_commit=True,
	)
	return {"queued": True, "run": run.name}


def stop(name):
	doc = master_connection(name)
	doc = frappe.get_doc("Master Instance", name, for_update=True)
	if doc.syncing_status != "In Progress":
		frappe.throw("Only an in-progress initial copy can be marked Failed")
	with system_write():
		for run_name in frappe.get_all(
			"Streaming Initial Sync Run",
			filters={"master_instance": name, "status": "In Progress"},
			pluck="name",
		):
			run = frappe.get_doc("Streaming Initial Sync Run", run_name, for_update=True)
			run.status = "Failed"
			run.lease_token = str(uuid.uuid4())
			run.save(ignore_permissions=True)
		doc.syncing_status = "Failed"
		doc.progress = 0
		doc.exceptions = "Initial copy was stopped by the operator. Previously committed records are preserved; use Continue."
		doc.save(ignore_permissions=True)
	return {"status": "Failed"}


class ObsoleteRun(Exception):
	pass


def locked_run(name, run_name, token):
	doc = frappe.get_doc("Master Instance", name, for_update=True)
	run = frappe.get_doc("Streaming Initial Sync Run", run_name, for_update=True)
	if doc.syncing_status != "In Progress" or run.status != "In Progress" or run.lease_token != token:
		raise ObsoleteRun
	return doc, run


def drain(doc, run, boundary, stage, token):
	for _ in range(500):
		exchange(doc)
		execute_captures(
			only_peer=doc.name,
			bootstrap=True,
			maximum_sequence=int(boundary),
			guard=lambda: locked_run(doc.name, run.name, token),
		)
		failed = frappe.db.exists(
			"Capture Event",
			{
				"instance_type": "Master Instance",
				"instance_name": doc.name,
				"status": "Failed",
				"event_sequence": ["<=", int(boundary)],
			},
		)
		if failed:
			frappe.throw("Initial-copy catch-up has a failed Capture Event; resolve it, then Continue")
		result = remote_call(
			doc,
			"arab_streaming.api.bootstrap.finish",
			**wire_identity(doc),
			export=run.remote_export,
			stage=stage,
		)
		pending = frappe.db.count(
			"Capture Event",
			{
				"instance_type": "Master Instance",
				"instance_name": doc.name,
				"status": ["in", ["Pending", "Failed"]],
				"event_sequence": ["<=", int(boundary)],
			},
		)
		if not result["outstanding"] and not pending:
			return
	frappe.throw("Catch-up exceeded the bounded job budget; use Continue to resume")


def run_initial(name, run_name, token):
	try:
		doc, run = locked_run(name, run_name, token)
		rows = frappe.parse_json(run.configuration)
		if not run.remote_export:
			result = remote_call(
				doc,
				"arab_streaming.api.bootstrap.prepare",
				**wire_identity(doc),
				request_id=run.name,
				configuration=rows,
			)
			with system_write():
				run.remote_export = result["export"]
				run.boundary_h0 = int(result["boundary_h0"])
				run.save(ignore_permissions=True)
				for item in result["counts"]:
					next(
						row for row in doc.initial_sync_doctypes if row.name == item["row_key"]
					).document_count = item["count"]
				doc.total_syncing_document = sum(row.document_count for row in doc.initial_sync_doctypes)
				doc.save(ignore_permissions=True)
		frappe.db.commit()
		if not run.history_drained:
			drain(doc, run, run.boundary_h0, "history", token)
			doc, run = locked_run(name, run_name, token)
			with system_write():
				run.history_drained = 1
				run.save(ignore_permissions=True)
			frappe.db.commit()
		for row in rows:
			while True:
				doc, run = locked_run(name, run_name, token)
				checkpoint = next(child for child in run.checkpoints if child.row_key == row["row_key"])
				if checkpoint.complete:
					frappe.db.commit()
					break
				result = remote_call(
					doc,
					"arab_streaming.api.bootstrap.page",
					**wire_identity(doc),
					export=run.remote_export,
					row_key=row["row_key"],
					offset=checkpoint.processed_count or 0,
				)
				if not result["items"]:
					expected = next(
						child.document_count
						for child in doc.initial_sync_doctypes
						if child.name == row["row_key"]
					)
					if (checkpoint.processed_count or 0) != expected:
						frappe.throw(
							"Initial snapshot ended before its durable count; reconcile the manifest"
						)
					with system_write():
						checkpoint.complete = 1
						run.save(ignore_permissions=True)
						next(
							child for child in doc.initial_sync_doctypes if child.name == row["row_key"]
						).synced = 1
						doc.save(ignore_permissions=True)
					frappe.db.commit()
					break
				# Commit each item with its checkpoint, under the controlled-stop fence.
				for item in result["items"]:
					doc, run = locked_run(name, run_name, token)
					checkpoint = next(child for child in run.checkpoints if child.row_key == row["row_key"])
					if int(item["position"]) < (checkpoint.processed_count or 0):
						continue
					if int(item["position"]) != (checkpoint.processed_count or 0):
						frappe.throw("Initial snapshot has a checkpoint gap")
					if digest(item["document"]) != item["payload_digest"]:
						frappe.throw("Initial snapshot digest mismatch")
					state = peer_state("Master Instance", name)
					with replay(state.pairing_id, state.execution_user):
						_status, reason = apply_document(
							doc,
							item["document"],
							"Skip" if row["skip_if_exists"] else "Update",
							ignore_links=bool(row["ignore_links"]),
							skip_links=bool(row["skip_links_if_exists"]),
							export_name=run.remote_export,
						)
					with system_write():
						checkpoint.processed_count = int(item["position"]) + 1
						checkpoint.diagnostic = reason
						run.save(ignore_permissions=True)
						done = sum(child.processed_count or 0 for child in run.checkpoints)
						doc.progress = min(99, 100 * done / max(doc.total_syncing_document or 0, 1))
						doc.save(ignore_permissions=True)
					frappe.db.commit()
		doc, run = locked_run(name, run_name, token)
		result = remote_call(
			doc, "arab_streaming.api.bootstrap.finish", **wire_identity(doc), export=run.remote_export
		)
		with system_write():
			run.boundary_h1 = int(result["boundary"])
			run.save(ignore_permissions=True)
		frappe.db.commit()
		drain(doc, run, result["boundary"], "boundary", token)
		doc, run = locked_run(name, run_name, token)
		for hook in frappe.get_hooks("arab_streaming_after_initial_sync"):
			frappe.get_attr(hook)(doc=doc, run=run)
		remote_call(
			doc,
			"arab_streaming.api.bootstrap.finish",
			**wire_identity(doc),
			export=run.remote_export,
			stage="close",
		)
		with system_write():
			run.status = "Complete"
			run.retention_hold = 0
			run.save(ignore_permissions=True)
			doc.syncing_status = "Complete"
			doc.progress = 100
			doc.exceptions = None
			doc.save(ignore_permissions=True)
		frappe.db.set_single_value("Streaming Settings", "strictly_validate_document_actions", 1)
		frappe.db.commit()
	except ObsoleteRun:
		frappe.db.rollback()
	except Exception as exc:
		frappe.db.rollback()
		try:
			doc, run = locked_run(name, run_name, token)
			with system_write():
				run.status = "Failed"
				run.save(ignore_permissions=True)
				doc.syncing_status = "Failed"
				doc.exceptions = str(exc)[:4000]
				doc.progress = 0
				doc.save(ignore_permissions=True)
			frappe.db.commit()
		except ObsoleteRun:
			frappe.db.rollback()


def restart(name):
	doc = master_connection(name)
	if doc.syncing_status == "In Progress":
		stop(name)
	for runname in frappe.get_all(
		"Streaming Initial Sync Run", filters={"master_instance": name, "retention_hold": 1}, pluck="name"
	):
		run = frappe.get_doc("Streaming Initial Sync Run", runname, for_update=True)
		if run.remote_export:
			remote_call(
				doc, "arab_streaming.api.bootstrap.abandon", **wire_identity(doc), export=run.remote_export
			)
		with system_write():
			run.status = "Abandoned"
			run.retention_hold = 0
			run.lease_token = str(uuid.uuid4())
			run.save(ignore_permissions=True)
	with system_write():
		doc = frappe.get_doc("Master Instance", name)
		doc.syncing_status = "Not Started"
		doc.save(ignore_permissions=True)
	return start(name)
