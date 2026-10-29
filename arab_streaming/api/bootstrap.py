import frappe

from arab_streaming.api.events import addressed
from arab_streaming.protocol.codec import snapshot
from arab_streaming.streaming.adapters import require_supported
from arab_streaming.streaming.exports import (
	authorized_export,
	count_rows,
	export_page,
	outstanding,
	prepare_export,
)
from arab_streaming.streaming.guards import system_write


@frappe.whitelist(methods=["POST"])
def count(protocol, version, pairing_id, source_installation_id, pairing_generation, configuration):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	return {"counts": count_rows(configuration, peer)}


@frappe.whitelist(methods=["POST"])
def prepare(
	protocol, version, pairing_id, source_installation_id, pairing_generation, request_id, configuration
):
	_peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	return prepare_export(state, request_id, configuration)


@frappe.whitelist(methods=["POST"])
def page(
	protocol, version, pairing_id, source_installation_id, pairing_generation, export, row_key, offset=0
):
	_peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	return export_page(state, export, row_key, offset)


@frappe.whitelist(methods=["POST"])
def dependency(
	protocol,
	version,
	pairing_id,
	source_installation_id,
	pairing_generation,
	doctype,
	name,
	export=None,
	root_doctype=None,
	root_name=None,
	root_event=None,
):
	_peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	require_supported(doctype)
	if export:
		authorized_export(state, export)
		data = frappe.db.get_value(
			"Streaming Snapshot Item",
			{"export": export, "reference_doctype": doctype, "reference_name": name},
			"data",
		)
		if not data:
			frappe.throw("This dependency is not part of the immutable initial manifest")
		from arab_streaming.streaming.policy import authorize_document

		document = frappe.parse_json(data)
		authorize_document(_peer, document, dependency=True)
		return {"document": document}
	if root_event:
		from arab_streaming.streaming.dependencies import event_dependency

		return {"document": event_dependency(_peer, root_event, doctype, name)}
	frappe.throw(
		"A captured event or immutable export is required to retrieve a dependency", frappe.PermissionError
	)


@frappe.whitelist(methods=["POST"])
def finish(
	protocol, version, pairing_id, source_installation_id, pairing_generation, export, stage="boundary"
):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	frappe.db.commit()
	if stage not in ("history", "boundary", "close"):
		frappe.throw("Unknown initial-copy boundary action")
	boundary = None
	if stage != "history":
		boundary = frappe.db.sql(
			"SELECT event_sequence FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE"
		)[0][0]
	manifest = authorized_export(state, export)
	if stage == "history":
		return {"boundary": str(manifest.boundary_h0), "outstanding": outstanding(peer, manifest.boundary_h0)}
	if not manifest.boundary_h1_set:
		manifest = frappe.get_doc("Streaming Export", export, for_update=True)
		if not manifest.boundary_h1_set:
			with system_write():
				manifest.boundary_h1 = boundary
				manifest.boundary_h1_set = 1
				manifest.save(ignore_permissions=True)
	remaining = outstanding(peer, manifest.boundary_h1)
	if stage == "close":
		if remaining:
			frappe.throw("Initial-copy catch-up still has unacknowledged dispatches")
		with system_write():
			manifest.status = "Complete"
			manifest.save(ignore_permissions=True)
	return {"boundary": str(manifest.boundary_h1), "outstanding": remaining}


@frappe.whitelist(methods=["POST"])
def start_copy(protocol, version, pairing_id, source_installation_id, pairing_generation, doctypes):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	if peer.doctype != "Master Instance" or not isinstance(doctypes, list) or not 1 <= len(doctypes) <= 50:
		frappe.throw("Only the paired master can request its branch initial copy", frappe.PermissionError)
	from arab_streaming.streaming.initial_sync import start
	from arab_streaming.streaming.policy import config_for

	if peer.syncing_status in ("In Progress", "Failed"):
		frappe.throw("Resolve or restart the current initial-copy run first")
	with system_write():
		peer.initial_sync_doctypes = []
		for doctype in doctypes:
			rule = config_for(peer, doctype, incoming=True)
			if not rule:
				frappe.throw("Initial copy direction/DocType is not enabled", frappe.PermissionError)
			peer.append(
				"initial_sync_doctypes",
				dict(
					doctype_name=doctype,
					conditions=rule.conditions,
					scope=rule.scope,
					branch_field=rule.branch_field,
					rule_id=rule.name,
					rule_revision=rule.revision,
					skip_if_exists=1,
					skip_links_if_exists=1,
				),
			)
		peer.save(ignore_permissions=True)
	return start(peer.name, paired_peer=peer)


@frappe.whitelist(methods=["POST"])
def abandon(protocol, version, pairing_id, source_installation_id, pairing_generation, export):
	_peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	manifest = frappe.get_doc("Streaming Export", export, for_update=True)
	if manifest.peer_state != state.name:
		frappe.throw("Export does not belong to this peer", frappe.PermissionError)
	# Expiry does not prevent explicit release of its retention hold.
	with system_write():
		manifest.status = "Abandoned"
		manifest.save(ignore_permissions=True)
	return {"status": "Abandoned"}


@frappe.whitelist(methods=["POST"])
def password_values(
	protocol, version, pairing_id, source_installation_id, pairing_generation, export, doctype, name
):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.protected import fetch

	return fetch(peer, state, export, doctype, name)
