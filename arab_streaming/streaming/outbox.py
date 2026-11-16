import json
from datetime import timezone

import frappe

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.adapters import SAFE_TYPES, condition_fields, validate_config
from arab_streaming.streaming.conditions import matches, parse
from arab_streaming.streaming.context import replay_source
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.ownership import validate_action
from arab_streaming.streaming.state import next_sequence


def active(doc):
	return (
		doc.doctype in SAFE_TYPES
		and frappe.conf.get("arab_streaming_enabled")
		and frappe.conf.get("arab_streaming_site_role") in ("Master", "Slave")
		and replay_source() is None
		and not any(
			frappe.flags.get(flag) for flag in ("in_install", "in_migrate", "in_patch", "in_setup_wizard")
		)
	)


def fence(doc, method=None, *args):
	if active(doc):
		frappe.db.sql("SELECT name FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE")
		if method == "before_validate":
			from arab_streaming.streaming.policy import check_source

			check_source(doc)
			from arab_streaming.streaming.stock import validate_action as validate_stock_action

			validate_stock_action(doc)
		validate_action(doc, method, *args)


def recipients(doc, method, *args):
	# A business request may already have an older repeatable-read snapshot.
	# Read committed configuration on an independent, read-only connection after
	# taking the source fence. Never commit the business transaction to refresh it.
	import pymysql

	parent_type = "Slave Instance" if frappe.conf.arab_streaming_site_role == "Master" else "Master Instance"
	connection = pymysql.connect(**frappe.db.get_connection_settings())
	try:
		with connection.cursor(pymysql.cursors.DictCursor) as cursor:
			cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
			cursor.execute(
				f"""SELECT peer.name, peer.restaurant_branch, state.name as state_name, state.pairing_id, state.pairing_generation
				FROM `tab{parent_type}` peer JOIN `tabStreaming Peer State` state
				ON state.instance_type=%s AND state.instance_name=peer.name WHERE peer.enabled=1""",
				(parent_type,),
			)
			peers = cursor.fetchall()
			for item in peers:
				peer = frappe._dict(
					name=item["name"], doctype=parent_type, restaurant_branch=item["restaurant_branch"]
				)
				state = frappe._dict(
					name=item["state_name"],
					pairing_id=item["pairing_id"],
					pairing_generation=item["pairing_generation"],
				)
				from arab_streaming.streaming.policy import config_for, matches_scope

				row = config_for(peer, doc.doctype, cursor=cursor)
				config = None
				if row:
					validate_config(row)
					flag = {
						"after_insert": "event_create",
						"on_update": "event_update",
						"on_submit": "event_submit",
						"on_cancel": "event_cancel",
						"on_trash": "event_delete",
						"after_rename": "event_rename",
					}.get(method)
					if flag and row.get(flag) == 0:
						continue
					if (
						(method != "on_update_after_submit" or row.sync_events_after_submit)
						and matches_scope(doc, row, peer.restaurant_branch, required=True)
						and matches(parse(row.conditions, condition_fields(doc.doctype)), doc)
					):
						from arab_streaming.streaming.policy import RULE_FIELDS

						config = {key: row.get(key) for key in RULE_FIELDS}
						config["field_mappings"] = row.get("field_mappings") or []
						config.update(rule_id=row.name, rule_revision=row.get("revision") or 0)
				if config is None:
					cursor.execute(
						"SELECT name, configuration FROM `tabStreaming Export` WHERE peer_state=%s AND status=%s",
						(state.name, "In Progress"),
					)
					exports = cursor.fetchall()
					previous = doc.get_doc_before_save()
					old_name = args[0] if method == "after_rename" and args else doc.name
					for export in exports:
						cursor.execute(
							"SELECT name FROM `tabStreaming Snapshot Item` WHERE export=%s AND reference_doctype=%s AND reference_name in (%s,%s) LIMIT 1",
							(export["name"], doc.doctype, doc.name, old_name),
						)
						selected = bool(cursor.fetchone())
						for initial in frappe.parse_json(export["configuration"]):
							if initial["doctype_name"] != doc.doctype:
								continue
							predicate = parse(initial.get("conditions"), condition_fields(doc.doctype))
							in_scope = matches(predicate, doc) or (
								previous is not None and matches(predicate, previous)
							)
							if initial.get("sync_from") and doc.creation:
								in_scope = in_scope and frappe.utils.get_datetime(
									doc.creation
								) >= frappe.utils.get_datetime(initial["sync_from"])
							selected = selected or in_scope
						if selected:
							config = {
								"doctype_name": doc.doctype,
								"priority": 100,
								"on_duplicated_insert": "Update",
							}
				if config is not None:
					yield peer, state, config
	finally:
		connection.rollback()
		connection.close()


def capture(doc, method=None, *args):
	if not active(doc):
		return
	if method == "on_update" and (doc.get_doc_before_save() is None or doc._action == "submit"):
		return
	fence(doc)
	selected = list(recipients(doc, method, *args))
	from arab_streaming.streaming.related import capture_change

	capture_change(doc, method, {(peer.doctype, peer.name) for peer, _state, _config in selected}, *args)
	if not selected:
		return
	operation = {
		"after_insert": "Create",
		"on_update": "Update",
		"on_submit": "Update",
		"on_update_after_submit": "Update",
		"on_cancel": "Cancel",
		"on_trash": "Delete",
		"after_rename": "Rename",
	}[method]
	data = {"document": snapshot(doc)}
	if operation == "Delete":
		data["base_digest"] = digest(data["document"])
	if operation == "Rename":
		data.update(old_name=args[0], new_name=args[1], merge=bool(args[2]))
	previous = doc.get_doc_before_save()
	if previous:
		before_digest = digest(snapshot(previous))
		for _peer, state, config in selected:
			tracked = (
				frappe.db.get_value(
					"Streaming Record State",
					{"peer_state": state.name, "reference_doctype": doc.doctype, "local_name": doc.name},
					["remote_digest", "local_digest"],
					as_dict=True,
				)
				if frappe.db.exists("DocType", "Streaming Record State")
				else None
			)
			if tracked and tracked.local_digest == before_digest:
				config["expected_remote_base"] = tracked.remote_digest
	if previous and operation in ("Update", "Cancel"):
		before = snapshot(previous)
		if operation == "Update" and digest(before) == digest(data["document"]):
			return
		data["base_digest"] = digest(before)
		data["changes"] = {k: v for k, v in data["document"].items() if before.get(k) != v}
		data["removed_fields"] = [k for k in before if k not in data["document"]]
	return store_event(doc, operation, selected, data=data, method=method)


def store_event(
	doc,
	operation,
	selected,
	data=None,
	copy_run=None,
	preserve_existing=False,
	method=None,
	_related_prepared=False,
	_frozen_versions=None,
):
	"""Persist one explicit snapshot using the same immutable outbox as normal saves."""
	data = data if data is not None else {"document": snapshot(doc)}
	from arab_streaming.streaming.dependencies import freeze, persist

	# Related associations are projected per peer, before freezing any payload.
	if not _related_prepared and not data.get("related_only") and operation in ("Create", "Update"):
		import copy

		from arab_streaming.streaming.related import bundle

		groups = {}
		for destination in selected:
			peer_data = copy.deepcopy(data)
			peer_data.update(bundle(destination[0], data["document"]))
			versions = (
				freeze(peer_data["document"], peer_data.get("related_records"), destination[0])
				if frappe.db.exists("DocType", "Streaming Document Version")
				else []
			)
			key = digest({"data": peer_data, "versions": [list(version) for version in versions]})
			group = groups.setdefault(key, {"data": peer_data, "versions": versions, "selected": []})
			group["selected"].append(destination)
		event = None
		for group in groups.values():
			event = store_event(
				doc,
				operation,
				group["selected"],
				group["data"],
				copy_run,
				preserve_existing,
				method,
				_related_prepared=True,
				_frozen_versions=group["versions"],
			)
		return event

	related = data.get("related_records") or []
	if data.get("related_change"):
		related = [data["related_change"]["document"]]
	versions = _frozen_versions
	if versions is None:
		versions = (
			freeze(data["document"], related, selected[0][0])
			if frappe.db.exists("DocType", "Streaming Document Version")
			else []
		)
	from arab_streaming.streaming.files import freeze as freeze_files

	files = freeze_files(data["document"])
	with system_write():
		sequence = next_sequence()
		event = frappe.get_doc(
			dict(
				doctype="Dispatch Event",
				reference_doctype=doc.doctype,
				reference_name=doc.name,
				event_type=operation,
				copy_run=copy_run,
				data=json.dumps(data, ensure_ascii=False),
				metadata={
					"engine": 2,
					**(
						{"copy_run": copy_run, "preserve_existing": int(preserve_existing)}
						if copy_run
						else {}
					),
					"files": files,
					"origin_site": frappe.db.get_value("Streaming Site State", "local", "installation_id"),
					"origin_document": doc.name,
				},
				created_on=frappe.utils.now_datetime(),
				event_sequence=sequence,
				created_by=frappe.session.user,
				document_event=method or "initial_copy",
				instances=[
					dict(
						instance_type=peer.doctype,
						instance_name=peer.name,
						pairing_id=state.pairing_id,
						pairing_generation=state.pairing_generation,
						doctype_config=json.dumps(config, ensure_ascii=False),
					)
					for peer, state, config in selected
				],
			)
		)
		event.insert(ignore_permissions=True)
		if versions:
			persist(event, versions)
	from arab_streaming.tasks import wakeup

	frappe.db.after_commit.add(wakeup)

	return event


def notify_persisted_change(before):
	"""Consumer service hook after an authorized atomic SQL update, before commit."""
	if not active(before):
		return
	after = frappe.get_doc(before.doctype, before.name)
	after._doc_before_save = before
	after._action = "save"
	return capture(after, "on_update_after_submit" if after.docstatus else "on_update")


def before_dbset(doc, method=None):
	if not active(doc):
		return
	before = frappe.get_doc(doc.doctype, doc.name)
	if digest(snapshot(before)) == digest(snapshot(doc)):
		return
	fence(doc, "before_validate")
	doc._arab_streaming_dbset_before = before


def after_dbset(doc, method=None):
	before = getattr(doc, "_arab_streaming_dbset_before", None)
	if before is None:
		return
	doc._arab_streaming_dbset_before = None
	notify_persisted_change(before)
