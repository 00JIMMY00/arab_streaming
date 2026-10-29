import frappe

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.adapters import (
	SAFE_TYPES,
	linked_records,
	missing_dependency,
	validate_document,
)
from arab_streaming.streaming.connections import peer_state
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.guards import system_write


def apply_document(
	peer,
	document,
	policy="Update",
	base_digest=None,
	ignore_links=False,
	skip_links=True,
	depth=0,
	seen=None,
	export_name=None,
	captured_config=None,
):
	import copy

	from arab_streaming.streaming.mapping import check_base, local_document, remember

	source_document = copy.deepcopy(document)
	from arab_streaming.streaming.policy import config_for

	config = config_for(peer, document["doctype"], incoming=True) if peer.get("restaurant_branch") else None
	# A preserve-existing copy needs no attachment/password/dependency transfers.
	if policy == "Skip":
		from arab_streaming.streaming.mapping import mapped_name

		local_name = mapped_name(peer, document["doctype"], document["name"])
		if frappe.db.exists(document["doctype"], local_name):
			frappe.get_doc(document["doctype"], local_name).check_permission("read")
			return "Skipped", "Existing target preserved by Skip policy"
	from arab_streaming.streaming.protected import apply as apply_passwords
	from arab_streaming.streaming.protected import import_values

	password_values = import_values(peer, document, export_name)
	from arab_streaming.streaming.files import attach, import_files

	import_files(peer, document, export_name)
	document = local_document(peer, document, config)
	doctype, name = document.get("doctype"), document.get("name")
	validate_document(document)
	desired_status = int(document.get("docstatus", 0))
	meta = frappe.get_meta(doctype)
	if desired_status and not meta.is_submittable:
		frappe.throw("Only submittable business documents can carry submitted/cancelled state")
	exists = bool(meta.issingle) or frappe.db.exists(doctype, name)
	target = frappe.get_doc(doctype, name) if exists else None
	if target:
		target.check_permission("read")
	if exists and policy == "Skip" and not meta.issingle:
		return "Skipped", "Existing target preserved by Skip policy"
	if exists and policy == "Throw Error" and not meta.issingle:
		frappe.throw("The target already exists and the captured policy is Throw Error")
	if target:
		target.check_permission("write")
		if not password_values and digest(snapshot(target)) == digest(document):
			remember(peer, source_document, target)
			return "Success", None
	check_base(peer, source_document, target, base_digest)
	if target and target.docstatus == 1 and desired_status == 2:
		target.cancel()
		remember(peer, source_document, target)
		return "Success", None
	if not ignore_links:
		seen = set() if seen is None else seen
		if depth > 10 or len(seen) > 200:
			frappe.throw("Dependency traversal limit reached")
		seen.add((doctype, name))
		for linked_type, linked_name, path in linked_records(document):
			identity = (linked_type, linked_name)
			if peer.get("name") and frappe.db.exists(
				"Capture Event",
				{
					"instance_type": peer.doctype,
					"instance_name": peer.name,
					"reference_doctype": linked_type,
					"reference_name": linked_name,
					"status": ["in", ["Failed", "Pending"]],
					"ignore": 0,
				},
			):
				frappe.throw(
					"Dependency has an unapplied Capture Event: "
					+ linked_type
					+ " "
					+ linked_name
					+ ". Resolve that event first."
				)
			if identity in seen or (skip_links and frappe.db.exists(*identity)):
				continue
			if linked_type not in SAFE_TYPES or (
				not peer.get("name") and linked_type in ("Company", "Warehouse", "Account", "Cost Center")
			):
				if not frappe.db.exists(*identity):
					missing_dependency(linked_type, linked_name, path)
				continue
			from arab_streaming.streaming.client import remote_call
			from arab_streaming.streaming.connections import wire_identity

			try:
				result = remote_call(
					peer,
					"arab_streaming.api.bootstrap.dependency",
					**wire_identity(peer),
					doctype=linked_type,
					name=linked_name,
					**({"export": export_name} if export_name else {}),
					root_doctype=source_document["doctype"],
					root_name=source_document["name"],
					root_event=getattr(frappe.local, "streaming_current_event", None),
				)
			except Exception as exc:
				from arab_streaming.streaming.client import RemoteError

				if isinstance(exc, RemoteError):
					raise RemoteError(f"Cannot fetch dependency {linked_type} {linked_name}: {exc}") from None
				raise
			if (result["document"].get("doctype"), result["document"].get("name")) != identity:
				frappe.throw("Dependency response does not match the requested catalog identity")
			apply_document(
				peer,
				result["document"],
				"Skip" if skip_links else "Update",
				depth=depth + 1,
				seen=seen,
				skip_links=skip_links,
				export_name=export_name,
			)
	doc = target or frappe.get_doc(dict(document, docstatus=0))
	values = {key: item for key, item in document.items() if key not in ("doctype", "name", "docstatus")}
	if target:
		if target.docstatus == 2:
			if desired_status != 2:
				frappe.throw("A cancelled target cannot be silently reopened")
			return "Skipped", "Target is already cancelled"
		if target.docstatus == 1 and desired_status == 0:
			frappe.throw("A submitted target cannot be silently reverted to draft")
		doc.update(values)
	from arab_streaming.streaming.naming import allow_series

	allow_series(doc)
	apply_passwords(doc, password_values)
	if meta.has_field("set_posting_time"):
		doc.set_posting_time = 1
	if doctype == "Item":
		doc.flags.dont_update_variants = True
	if target:
		for child in doc.get_all_children():
			if not frappe.db.exists(child.doctype, child.name):
				child.set("__islocal", True)
		doc.save()
	else:
		doc.insert(set_name=name, set_child_names=False)
	if desired_status in (1, 2) and doc.docstatus == 0:
		doc.submit()
	if desired_status == 2 and doc.docstatus == 1:
		doc.cancel()
	attach(document)
	remember(peer, source_document, doc)
	return "Success", None


def apply_capture(capture, peer):
	data = frappe.parse_json(capture.data)
	config = frappe.parse_json(capture.doctype_config)
	state = peer_state(peer.doctype, peer.name)
	from arab_streaming.streaming.policy import authorize_document

	if isinstance(data.get("document"), dict):
		authorize_document(peer, data["document"], incoming=True)
	frappe.local.streaming_current_event = capture.remote_dispatch_event
	with replay(state.pairing_id, state.execution_user):
		if capture.event_type in ("Create", "Update", "Cancel"):
			if (
				data.get("document", {}).get("doctype") != capture.reference_doctype
				or data["document"].get("name") != capture.reference_name
			):
				frappe.throw("Capture reference does not match its document")
			if (
				capture.event_type == "Update"
				and not frappe.db.exists(capture.reference_doctype, capture.reference_name)
				and not config.get("rule_id")
			):
				frappe.throw("Update target is missing; its earlier Create must be applied first")
			if capture.event_type == "Cancel" and data["document"].get("docstatus") != 2:
				frappe.throw("Cancel must carry the cancelled source state")
			metadata = frappe.parse_json(capture.metadata or "{}")
			policy = config.get("on_duplicated_insert") or "Update"
			if metadata.get("copy_run") and metadata.get("preserve_existing") == 1:
				policy = "Skip"  # A copy may only tighten the published overwrite policy.
			return apply_document(
				peer,
				data["document"],
				policy,
				base_digest=config.get("expected_remote_base") or data.get("base_digest")
				if capture.event_type in ("Update", "Cancel")
				else None,
				captured_config=config,
			)
		if capture.event_type == "Delete":
			from arab_streaming.streaming.mapping import check_base, mapped_name

			local_name = mapped_name(peer, capture.reference_doctype, capture.reference_name)
			if not frappe.db.exists(capture.reference_doctype, local_name):
				return "Skipped", "Delete target is already absent"
			target = frappe.get_doc(capture.reference_doctype, local_name)
			check_base(
				peer, data["document"], target, config.get("expected_remote_base") or data.get("base_digest")
			)
			frappe.delete_doc(capture.reference_doctype, local_name)
			return "Success", None
		if capture.event_type == "Rename":
			old, new = data.get("old_name"), data.get("new_name")
			if not old or new != capture.reference_name:
				frappe.throw("Invalid rename identity")
			from arab_streaming.streaming.mapping import mapped_name, remember

			old, new = (
				mapped_name(peer, capture.reference_doctype, old),
				mapped_name(peer, capture.reference_doctype, new),
			)
			if old == new or (
				not frappe.db.exists(capture.reference_doctype, old)
				and frappe.db.exists(capture.reference_doctype, new)
			):
				return "Skipped", "Rename is already reflected on the target"
			frappe.rename_doc(capture.reference_doctype, old, new, merge=bool(data.get("merge")))
			remember(peer, data["document"], frappe.get_doc(capture.reference_doctype, new))
			return "Success", None
		frappe.throw("This adapter does not support the requested lifecycle operation")


def execute_captures(only_peer=None, bootstrap=False, maximum_sequence=None, guard=None):
	parent_type = "Slave Instance" if frappe.conf.arab_streaming_site_role == "Master" else "Master Instance"
	for name in frappe.get_all(parent_type, filters={"enabled": 1}, pluck="name"):
		if only_peer and name != only_peer:
			continue
		peer = frappe.get_doc(parent_type, name)
		if (
			parent_type == "Master Instance"
			and peer.syncing_status in ("In Progress", "Failed")
			and not bootstrap
		):
			continue
		for _ in range(25):
			state = peer_state(parent_type, name)
			try:
				frappe.db.sql(
					"SELECT name FROM `tabStreaming Peer State` WHERE name=%s FOR UPDATE NOWAIT",
					(state.name,),
				)
			except frappe.QueryTimeoutError:
				frappe.db.rollback()
				break
			pending_filters = {
				"instance_type": parent_type,
				"instance_name": name,
				"status": ["in", ["Pending", "Failed"]],
			}
			if maximum_sequence is not None:
				pending_filters["event_sequence"] = ["<=", maximum_sequence]
			pending_filters["ignore"] = 0
			rows = frappe.get_all(
				"Capture Event",
				filters=pending_filters,
				fields=[
					"name",
					"reference_doctype",
					"reference_name",
					"event_type",
					"data",
					"next_attempt_at",
					"event_sequence",
				],
				order_by="event_sequence asc",
				limit_page_length=1000,
			)
			aliases = {}

			def identity(key):
				while key in aliases:
					key = aliases[key]
				return key

			for row in rows:
				if row.event_type == "Rename":
					data = frappe.parse_json(row.data)
					old = (row.reference_doctype, data.get("old_name"))
					new = (row.reference_doctype, data.get("new_name"))
					if identity(old) != identity(new):
						aliases[identity(new)] = identity(old)
			heads = {}
			for row in rows:
				key = identity((row.reference_doctype, row.reference_name))
				if key not in heads:
					heads[key] = row
			due = [
				row
				for row in heads.values()
				if not row.next_attempt_at or row.next_attempt_at <= frappe.utils.now_datetime()
			]
			if not due:
				frappe.db.commit()
				break
			selected = min(due, key=lambda r: r.event_sequence)
			capture = frappe.get_doc("Capture Event", selected.name, for_update=True)
			if guard:
				guard()
			frappe.db.savepoint("streaming_apply")
			try:
				status, reason = apply_capture(capture, peer)
				with system_write():
					capture.status = status
					capture.exception = reason
					capture.next_attempt_at = None
					capture.save(ignore_permissions=True)
				frappe.db.commit()
			except Exception as exc:
				frappe.db.rollback(save_point="streaming_apply")
				frappe.db.set_value(
					"Capture Event",
					capture.name,
					{
						"status": "Failed",
						"exception": (str(exc) or type(exc).__name__)[:4000],
						"retries": (capture.retries or 0) + 1,
						"next_attempt_at": frappe.utils.add_to_date(
							frappe.utils.now_datetime(),
							seconds=min(300, 2 ** min((capture.retries or 0) + 1, 8)),
						),
					},
				)
				frappe.db.commit()
				continue
