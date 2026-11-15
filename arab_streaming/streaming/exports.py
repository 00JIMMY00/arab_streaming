import json
import time

import frappe

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.adapters import SAFE_TYPES, condition_fields, linked_records, require_supported
from arab_streaming.streaming.conditions import filters, parse
from arab_streaming.streaming.guards import require_system_write, system_write


def write_snapshot_items(items):
	"""Persist internal immutable artifacts in batches; business saves stay native."""
	require_system_write()
	fields = [
		"name",
		"creation",
		"modified",
		"owner",
		"modified_by",
		"docstatus",
		"export",
		"row_key",
		"position",
		"reference_doctype",
		"reference_name",
		"data",
		"files",
		"protected_version",
		"payload_digest",
	]
	now = frappe.utils.now_datetime()
	values = []
	for item in items:
		if not isinstance(item.get("row_key"), str) or not 1 <= len(item["row_key"]) <= 140:
			frappe.throw("Invalid snapshot row identity")
		values.append(
			[
				frappe.generate_hash(length=20),
				now,
				now,
				frappe.session.user,
				frappe.session.user,
				0,
				*[item.get(field) for field in fields[6:]],
			]
		)
	frappe.db.bulk_insert("Streaming Snapshot Item", fields, values, chunk_size=4)


def selection(row):
	require_supported(row.get("doctype_name"))
	predicate = filters(parse(row.get("conditions"), condition_fields(row["doctype_name"])))
	if row.get("sync_from") and not frappe.get_meta(row["doctype_name"]).issingle:
		predicate.append(["creation", ">=", frappe.utils.get_datetime(row["sync_from"])])
	existing = row.get("existing_names")
	if existing is not None:
		if (
			row.get("skip_if_exists") not in (1, True)
			or not isinstance(existing, list)
			or len(existing) > 10000
			or any(not isinstance(name, str) or not name for name in existing)
		):
			frappe.throw("Invalid preserve-existing snapshot selection")
		if existing:
			predicate.append(["name", "not in", existing])
	return predicate


def rows_config(configuration):
	if not isinstance(configuration, list) or not 1 <= len(configuration) <= 50:
		frappe.throw("Initial copy needs between 1 and 50 configured rows")
	seen = set()
	for row in configuration:
		if not isinstance(row, dict) or not row.get("row_key") or row["row_key"] in seen:
			frappe.throw("Invalid initial-copy row identity")
		selection(row)
		seen.add(row["row_key"])
	return configuration


def count_rows(configuration, peer=None):
	from arab_streaming.streaming.export_policy import selected_names

	result = []
	for row in rows_config(configuration):
		if peer:
			count = sum(1 for _ in selected_names(peer, row))
		else:
			count = frappe.db.count(row["doctype_name"], selection(row))
		result.append({"row_key": row["row_key"], "count": count})
	return result


def prepare_export(state, request_id, configuration):
	if not frappe.conf.get("arab_streaming_enabled"):
		frappe.throw("The master streaming engine must be enabled before preparing a consistent export")
	configuration = rows_config(configuration)
	# Authentication/permission lookups may have opened an older repeatable-read
	# snapshot. Start the export transaction before acquiring the source fence.
	frappe.db.commit()
	# Every supported business write takes this same transactional fence before capture.
	boundary = frappe.db.sql(
		"SELECT event_sequence FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE"
	)[0][0]
	existing = frappe.db.get_value("Streaming Export", {"request_id": request_id})
	if existing:
		export = authorized_export(state, existing)
		if frappe.parse_json(export.configuration) != configuration:
			frappe.throw("A resumed initial copy must use its original configuration")
	else:
		start, total = time.monotonic(), 0
		dependencies = set()
		budget = {"bytes": 0, "started": start, "items": []}
		with system_write():
			export = frappe.get_doc(
				dict(
					doctype="Streaming Export",
					peer_state=state.name,
					request_id=request_id,
					configuration=json.dumps(configuration, ensure_ascii=False),
					boundary_h0=boundary,
					expires_at=frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=24),
				)
			)
			export.insert(ignore_permissions=True)
			for row in configuration:
				# One repeatable-read snapshot supplies all immutable document versions.
				from arab_streaming.streaming.export_policy import selected_names

				peer = frappe.get_doc(state.instance_type, state.instance_name)
				from arab_streaming.streaming.policy import config_for

				rule = config_for(peer, row["doctype_name"])
				names = list(selected_names(peer, row))
				for position, name in enumerate(names):
					total += 1
					doc = frappe.get_doc(row["doctype_name"], name)
					doc.check_permission("read")
					document = snapshot(doc)
					data = json.dumps(document, ensure_ascii=False)
					from arab_streaming.streaming.files import freeze as freeze_files

					files = freeze_files(document)
					from arab_streaming.streaming.protected import freeze as freeze_passwords

					protected_version = (
						freeze_passwords(document) if rule and rule.include_passwords else None
					)
					if len(data.encode()) > 1_000_000:
						frappe.throw("One initial snapshot document exceeds the supported payload limit")
					budget["bytes"] += len(data.encode())
					if total > 10000 or budget["bytes"] > 64 * 1024 * 1024 or time.monotonic() - start > 25:
						frappe.throw(
							"Initial snapshot scope exceeds the current bounded export limits; narrow the row conditions"
						)
					budget["items"].append(
						dict(
							doctype="Streaming Snapshot Item",
							export=export.name,
							row_key=row["row_key"],
							position=position,
							reference_doctype=doc.doctype,
							reference_name=doc.name,
							data=data,
							files=json.dumps(files),
							protected_version=protected_version,
							payload_digest=digest(document),
						)
					)
					if not row.get("ignore_links"):
						store_dependencies(export.name, document, dependencies, budget)
			write_snapshot_items(budget["items"])
			export.total_count = total
			export.save(ignore_permissions=True)
	counts = [
		{
			"row_key": row["row_key"],
			"count": frappe.db.count(
				"Streaming Snapshot Item", {"export": export.name, "row_key": row["row_key"]}
			),
		}
		for row in configuration
	]
	return {"export": export.name, "boundary_h0": str(export.boundary_h0), "counts": counts}


def store_dependencies(export_name, document, seen, budget, depth=0):
	if depth > 10:
		frappe.throw("Initial snapshot dependency traversal limit reached")
	require_supported(document["doctype"])
	for linked_type, name, _path in linked_records(document):
		if linked_type not in SAFE_TYPES:
			continue
		identity = (linked_type, name)
		if identity in seen:
			continue
		if len(seen) >= 10000:
			frappe.throw("Initial snapshot dependency traversal limit reached")
		seen.add(identity)
		doc = frappe.get_doc(*identity)
		doc.check_permission("read")
		data = snapshot(doc)
		encoded = json.dumps(data, ensure_ascii=False)
		from arab_streaming.streaming.files import freeze as freeze_files

		files = freeze_files(data)
		budget["bytes"] += len(encoded.encode())
		if budget["bytes"] > 64 * 1024 * 1024 or time.monotonic() - budget["started"] > 25:
			frappe.throw("Initial dependency snapshot exceeds the bounded export budget")
		if len(encoded.encode()) > 1_000_000:
			frappe.throw("One initial dependency exceeds the supported payload limit")
		budget["items"].append(
			dict(
				doctype="Streaming Snapshot Item",
				export=export_name,
				row_key="_dependency",
				position=len(seen),
				reference_doctype=doc.doctype,
				reference_name=doc.name,
				data=encoded,
				files=json.dumps(files),
				payload_digest=digest(data),
			)
		)
		store_dependencies(export_name, data, seen, budget, depth + 1)


def authorized_export(state, name):
	export = frappe.get_doc("Streaming Export", name)
	if export.peer_state != state.name:
		frappe.throw("Export does not belong to this peer", frappe.PermissionError)
	if export.expires_at < frappe.utils.now_datetime():
		frappe.throw("This immutable export has expired; restart initial copy with a new run")
	return export


def export_page(state, name, row_key, offset=0):
	export = authorized_export(state, name)
	if row_key not in {row["row_key"] for row in frappe.parse_json(export.configuration)}:
		frappe.throw("Unknown export row")
	if int(offset) < 0:
		frappe.throw("Invalid export offset")
	rows = frappe.get_all(
		"Streaming Snapshot Item",
		filters={"export": name, "row_key": row_key, "position": [">=", int(offset)]},
		fields=["position", "data", "payload_digest"],
		order_by="position",
		limit_page_length=20,
	)
	items, size = [], 0
	for row in rows:
		size += len(row.data.encode())
		if size > 3_500_000:
			break
		items.append(
			{
				"position": row.position,
				"document": frappe.parse_json(row.data),
				"payload_digest": row.payload_digest,
			}
		)
	return {"items": items}


def outstanding(peer, boundary):
	return frappe.db.sql(
		"""SELECT count(*) FROM `tabDispatch Event Instance` child
		JOIN `tabDispatch Event` event ON event.name=child.parent
		WHERE child.instance_type=%s AND child.instance_name=%s AND child.synced=0 AND child.`ignore`=0
		AND event.event_sequence<=%s""",
		(peer.doctype, peer.name, int(boundary)),
	)[0][0]
