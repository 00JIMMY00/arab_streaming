"""Frozen dependency versions belong to the same business transaction as an event."""

import json
import time

import frappe

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.adapters import SAFE_TYPES, linked_records
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.policy import authorize_document


def freeze(document):
	pending = [(document, 0)]
	seen = {(document["doctype"], document["name"])}
	versions = []
	size = 0
	start = time.monotonic()
	while pending:
		current, depth = pending.pop()
		if depth > 10:
			frappe.throw("Dependency depth exceeds 10; review this rule dependency graph")
		for dt, name, _path in linked_records(current):
			identity = (dt, name)
			if identity in seen or dt not in SAFE_TYPES:
				continue
			seen.add(identity)
			if len(seen) > 200 or time.monotonic() - start > 20:
				frappe.throw("Dependency snapshot budget exceeded; narrow the selected workflow")
			if not frappe.db.exists(dt, name):
				continue
			doc = frappe.get_doc(dt, name)
			data = snapshot(doc)
			encoded = json.dumps(data, ensure_ascii=False)
			size += len(encoded.encode())
			if size > 16 * 1024 * 1024:
				frappe.throw("Dependency snapshot exceeds 16 MiB")
			versions.append((dt, name, digest(data), encoded))
			pending.append((data, depth + 1))
	return versions


def persist(event, versions):
	with system_write():
		for dt, name, checksum, data in versions:
			from arab_streaming.streaming.files import freeze as freeze_files

			files = freeze_files(frappe.parse_json(data))
			checksum = digest({"document": frappe.parse_json(data), "files": files})
			if not frappe.db.exists("Streaming Document Version", checksum):
				frappe.get_doc(
					dict(
						doctype="Streaming Document Version",
						name=checksum,
						reference_doctype=dt,
						reference_name=name,
						data=data,
						payload_digest=checksum,
						files=json.dumps(files),
					)
				).insert(ignore_permissions=True, set_name=checksum)
			frappe.get_doc(
				dict(
					doctype="Streaming Event Dependency",
					dispatch_event=event.name,
					reference_doctype=dt,
					reference_name=name,
					document_version=checksum,
				)
			).insert(ignore_permissions=True)


def event_dependency(peer, event_id, doctype, name):
	event = frappe.get_doc("Dispatch Event", event_id)
	if not any(
		row.instance_type == peer.doctype and row.instance_name == peer.name for row in event.instances
	):
		frappe.throw("Event is not addressed to this peer", frappe.PermissionError)
	document = frappe.parse_json(event.data).get("document")
	# Validate the frozen root against current direction/branch authorization.
	authorize_document(peer, document)
	version = frappe.db.get_value(
		"Streaming Event Dependency",
		{"dispatch_event": event.name, "reference_doctype": doctype, "reference_name": name},
		"document_version",
	)
	if not version:
		frappe.throw("Dependency is not in the captured event manifest", frappe.PermissionError)
	data = frappe.parse_json(frappe.db.get_value("Streaming Document Version", version, "data"))
	if not frappe.has_permission(doctype, "read"):
		frappe.throw("Integration user cannot read the dependency", frappe.PermissionError)
	authorize_document(peer, data, dependency=True)
	return data
