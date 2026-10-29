"""Explicit identity mappings and canonical conflict checkpoints."""

import copy

import frappe

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.guards import system_write


def state_for(peer):
	if not peer.get("name"):
		return None
	name = frappe.db.get_value(
		"Streaming Peer State", {"instance_type": peer.doctype, "instance_name": peer.name}
	)
	return frappe.get_doc("Streaming Peer State", name) if name else None


def mapped_name(peer, doctype, name):
	if not peer.get("name"):
		return name
	return (
		frappe.db.get_value(
			"Streaming Value Mapping",
			{
				"instance_type": peer.doctype,
				"instance_name": peer.name,
				"reference_doctype": doctype,
				"remote_name": name,
			},
			"local_name",
		)
		or name
	)


def local_document(peer, document, config=None):
	result = copy.deepcopy(document)

	def walk(doc):
		meta = frappe.get_meta(doc["doctype"])
		for f in meta.fields:
			value = doc.get(f.fieldname)
			if f.fieldtype in ("Table", "Table MultiSelect"):
				for child in value or []:
					walk(child)
			elif value and f.fieldtype in ("Link", "Dynamic Link"):
				kind = f.options if f.fieldtype == "Link" else doc.get(f.options)
				doc[f.fieldname] = mapped_name(peer, kind, value)

	walk(result)
	result["name"] = mapped_name(peer, document["doctype"], document["name"])
	meta = frappe.get_meta(document["doctype"])
	if result["name"] != document["name"] and (meta.autoname or "").startswith("field:"):
		result[meta.autoname.split(":", 1)[1]] = result["name"]
	for row in (config or {}).get("field_mappings") or []:
		source, target = row["source_field"], row["target_field"]
		if source in result:
			result[target] = result.pop(source)
	return result


def record(peer, doctype, remote_name):
	state = state_for(peer)
	if not state:
		return None
	name = frappe.db.get_value(
		"Streaming Record State",
		{"peer_state": state.name, "reference_doctype": doctype, "remote_name": remote_name},
	)
	return frappe.get_doc("Streaming Record State", name) if name else None


def check_base(peer, document, target, base_digest):
	if not base_digest or not target:
		return
	tracked = record(peer, document["doctype"], document["name"])
	current = digest(snapshot(target))
	if current == base_digest:
		return
	if tracked:
		if tracked.remote_digest == digest(document) and tracked.local_digest == current:
			return
		if tracked.remote_digest != base_digest or tracked.local_digest != current:
			frappe.throw(
				"Synchronization conflict: the source base or local record changed. Review the Capture Event before retrying."
			)
	elif current != base_digest:
		frappe.throw("Target changed outside its source history; reconcile the conflict before retrying")


def remember(peer, source, target, event=None):
	state = state_for(peer)
	if not state:
		return
	with system_write():
		tracked = record(peer, source["doctype"], source["name"])
		if not tracked:
			tracked = frappe.get_doc(
				dict(
					doctype="Streaming Record State",
					peer_state=state.name,
					reference_doctype=source["doctype"],
					remote_name=source["name"],
					local_name=target.name,
				)
			)
		tracked.remote_digest = digest(source)
		tracked.local_digest = digest(snapshot(target))
		tracked.last_event = event
		tracked.save(ignore_permissions=True)
