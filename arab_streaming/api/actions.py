import frappe

from arab_streaming.streaming import connections


@frappe.whitelist(methods=["POST"])
def eligible_branches(name):
	return connections.eligible_branches(name)


@frappe.whitelist(methods=["POST"])
def link(name, branch):
	return connections.link_connection(name, branch)


@frappe.whitelist(methods=["POST"])
def ping(name):
	return connections.ping_connection(name)


@frappe.whitelist(methods=["POST"])
def disconnect(name):
	return connections.disconnect_connection(name)


@frappe.whitelist(methods=["POST"])
def set_enabled(doctype, name, enabled):
	enabled = frappe.utils.cint(enabled)
	if enabled not in (0, 1) or doctype not in ("Master Instance", "Slave Instance"):
		frappe.throw("Invalid streaming participation change")
	if doctype == "Master Instance":
		doc = connections.master_connection(name)
		if not enabled:
			return connections.disconnect_connection(name, disable=True)
		doc.enabled = 1
		connections.save_connection(doc)
		return connections.link_connection(name, doc.restaurant_branch)
	from arab_streaming.streaming.guards import require_administrator, system_write

	require_administrator()
	doc = frappe.get_doc(doctype, name)
	with system_write():
		doc.enabled = enabled
		if not enabled:
			doc.connection_status = "Disconnected"
		doc.save(ignore_permissions=True)
	return {"enabled": enabled}


@frappe.whitelist(methods=["POST"])
def count(name):
	from arab_streaming.streaming.initial_sync import counts

	return counts(name)


@frappe.whitelist(methods=["POST"])
def sync(name):
	from arab_streaming.streaming.initial_sync import start

	return start(name)


@frappe.whitelist(methods=["POST"])
def continue_sync(name):
	from arab_streaming.streaming.initial_sync import start

	return start(name, continuing=True)


@frappe.whitelist(methods=["POST"])
def fail_sync(name):
	from arab_streaming.streaming.initial_sync import stop

	return stop(name)


@frappe.whitelist(methods=["POST"])
def execute(name=None):
	frappe.only_for("System Manager")
	frappe.enqueue(
		"arab_streaming.tasks.execute_only",
		enqueue_after_commit=True,
		job_id="streaming-execute",
		deduplicate=True,
	)
	return {"queued": True}


@frappe.whitelist(methods=["POST"])
def fetch(name):
	connections.master_connection(name)
	frappe.enqueue(
		"arab_streaming.tasks.fetch_only",
		name=name,
		enqueue_after_commit=True,
		job_id="streaming-fetch-" + name,
		deduplicate=True,
	)
	return {"queued": True}


@frappe.whitelist(methods=["POST"])
def retry_capture(name):
	frappe.only_for("System Manager")
	doc = frappe.get_doc("Capture Event", name, for_update=True)
	from arab_streaming.streaming.feedback import request_retry

	return request_retry(doc)


@frappe.whitelist(methods=["POST"])
def ignore_capture(name, reason):
	frappe.only_for("System Manager")
	from arab_streaming.streaming.guards import system_write

	if not isinstance(reason, str) or not reason.strip():
		frappe.throw("An operational reason is required")
	doc = frappe.get_doc("Capture Event", name, for_update=True)
	if doc.status not in ("Pending", "Failed"):
		frappe.throw("Only Pending or Failed captures can be ignored")
	with system_write():
		doc.status, doc.ignore = "Ignored", 1
		doc.save(ignore_permissions=True)
	doc.add_comment("Comment", text="Ignored: " + reason.strip())
	return execute()


@frappe.whitelist(methods=["POST"])
def ignore_destination(name, destination, reason):
	frappe.only_for("System Manager")
	from arab_streaming.streaming.guards import system_write

	if not isinstance(reason, str) or not reason.strip():
		frappe.throw("An operational reason is required")
	doc = frappe.get_doc("Dispatch Event", name, for_update=True)
	row = next((row for row in doc.instances if row.name == destination), None)
	if not row or row.synced or row.ignore:
		frappe.throw("Only an outstanding unsynced destination can be ignored")
	with system_write():
		row.ignore = 1
		doc.save(ignore_permissions=True)
	doc.add_comment("Comment", text="Destination " + row.instance_name + " ignored: " + reason.strip())
	return {"status": doc.status}


@frappe.whitelist(methods=["POST"])
def restart_sync(name):
	from arab_streaming.streaming.initial_sync import restart

	return restart(name)


@frappe.whitelist(methods=["POST"])
def reconcile_stock(doctype, name):
	frappe.only_for("System Manager")
	if doctype not in ("Master Instance", "Slave Instance"):
		frappe.throw("Invalid connection")
	peer = frappe.get_doc(doctype, name)
	peer.check_permission("write")
	from arab_streaming.streaming.stock import reconcile

	return reconcile(peer, force=True) or {"status": "No incoming stock rules"}


@frappe.whitelist(methods=["POST"])
def refresh_dispatch(name):
	frappe.only_for("System Manager")
	from arab_streaming.streaming.feedback import refresh_results

	doc = frappe.get_doc("Dispatch Event", name)
	doc.check_permission("read")
	for row in doc.instances:
		if row.synced and not row.ignore:
			peer = frappe.get_doc(row.instance_type, row.instance_name)
			refresh_results(peer, event_name=doc.name)
	return {"refreshed": True}


@frappe.whitelist(methods=["POST"])
def retry_dispatch(name, destination):
	frappe.only_for("System Manager")
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import wire_identity
	from arab_streaming.streaming.feedback import refresh_results
	from arab_streaming.streaming.transport import envelope

	doc = frappe.get_doc("Dispatch Event", name)
	doc.check_permission("read")
	row = next((row for row in doc.instances if row.name == destination), None)
	if not row or not row.synced or row.ignore:
		frappe.throw("Select a delivered destination to retry on its receiver")
	peer = frappe.get_doc(row.instance_type, row.instance_name)
	result = remote_call(
		peer,
		"arab_streaming.api.events.retry",
		**wire_identity(peer),
		event_id=doc.name,
		payload_digest=envelope(doc, row)["payload_digest"],
	)
	refresh_results(peer, event_name=doc.name)
	return result
