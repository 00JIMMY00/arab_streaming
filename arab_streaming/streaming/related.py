"""Contact/Address ownership follows Dynamic Links, including non-primary records.

Changes travel in the owning business record's ordered, authorized event lane.
The owner is not saved just to notify its peers. Each peer receives only its
authorized associations, and existing associations outside that scope survive.
"""

import copy

import frappe

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.adapters import SAFE_TYPES, condition_fields
from arab_streaming.streaming.conditions import matches, parse
from arab_streaming.streaming.policy import authorize_document, config_for, matches_scope

ASSOCIATED_TYPES = frozenset({"Contact", "Address"})
MAX_RELATED = 200


def identity(document):
	return document["doctype"], document["name"]


def owners(document):
	if document.get("doctype") not in ASSOCIATED_TYPES:
		return set()
	return {
		(row.get("link_doctype"), row.get("link_name"))
		for row in document.get("links") or []
		if row.get("link_doctype") and row.get("link_name")
	}


def permitted(peer, document, incoming=False, operation=None):
	if document.get("doctype") not in SAFE_TYPES:
		return False
	rule = config_for(peer, document["doctype"], incoming=incoming)
	return bool(
		rule
		and (not operation or rule.get("event_" + operation.lower()) != 0)
		and matches_scope(document, rule, peer.restaurant_branch)
		and matches(parse(rule.get("conditions"), condition_fields(document["doctype"])), document)
	)


def project(document, allowed):
	result = copy.deepcopy(document)
	result["links"] = [
		row for row in result.get("links") or [] if (row.get("link_doctype"), row.get("link_name")) in allowed
	]
	return result


def contexts(peer, documents, root, operation=None):
	result = {identity(root): root}
	for key in sorted(set().union(*(owners(doc) for doc in documents))):
		if key in result or key[0] not in SAFE_TYPES or not frappe.db.exists(*key):
			continue
		doc = snapshot(frappe.get_doc(*key))
		if permitted(peer, doc, operation=operation):
			result[key] = doc
	return result


def bundle(peer, root):
	"""Freeze all reverse associations when a root is created/copied/saved."""
	if root["doctype"] in ASSOCIATED_TYPES:
		return {}
	rows = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": root["doctype"],
			"link_name": root["name"],
			"parenttype": ["in", sorted(ASSOCIATED_TYPES)],
			"parentfield": "links",
		},
		fields=["parenttype", "parent"],
		limit_page_length=MAX_RELATED + 1,
	)
	keys = sorted({(row.parenttype, row.parent) for row in rows})
	if len(keys) > MAX_RELATED:
		frappe.throw("One record has more than 200 linked contacts/addresses; split this workflow")
	documents = [snapshot(frappe.get_doc(*key)) for key in keys]
	if not documents:
		return {}
	roots = contexts(peer, documents, root)
	return {
		"related_roots": list(roots.values()),
		"related_records": [project(doc, roots) for doc in documents],
	}


def capture_change(doc, method, direct_peers, *args):
	"""Use both previous and current owners so unlinking is a real update."""
	if doc.doctype not in ASSOCIATED_TYPES:
		return
	from arab_streaming.streaming.outbox import recipients, store_event

	after = snapshot(doc)
	previous = doc.get_doc_before_save()
	before = snapshot(previous) if previous else None
	if before and digest(before) == digest(after) and method == "on_update":
		return
	keys = owners(after) | owners(before or {})
	if len(keys) > MAX_RELATED:
		frappe.throw("Too many linked owners in one contact/address change")
	operation = {
		"after_insert": "Create",
		"on_update": "Update",
		"on_trash": "Delete",
		"after_rename": "Rename",
	}.get(method)
	if not operation:
		return
	by_peer = {}
	for key in sorted(keys):
		if key[0] not in SAFE_TYPES or not frappe.db.exists(*key):
			continue
		owner = frappe.get_doc(*key)
		for peer, state, config in recipients(owner, method):
			peer_key = peer.doctype, peer.name
			if peer_key not in direct_peers and peer_key not in by_peer:
				by_peer[peer_key] = (owner, peer, state, config)
	for owner, peer, state, config in by_peer.values():
		root = snapshot(owner)
		roots = contexts(peer, [after, before or {}], root, operation)
		change = {"operation": operation, "document": project(after, roots)}
		if before:
			projected_before = project(before, roots)
			if owners(projected_before):
				change["before"] = projected_before
		if operation == "Delete":
			change["before"] = change["document"]
		if operation == "Rename":
			change.update(old_name=args[0], new_name=args[1], merge=bool(args[2]))
		store_event(
			owner,
			"Update",
			[(peer, state, config)],
			data={
				"document": root,
				"related_only": 1,
				"related_roots": list(roots.values()),
				"related_change": change,
			},
			method="linked_record_" + method,
		)


def validate(peer, data, incoming=True):
	"""A linked payload cannot grant access to a different owner or direction."""
	root = data["document"]
	roots = data.get("related_roots") or []
	records = data.get("related_records") or []
	change = data.get("related_change")
	if not isinstance(roots, list) or not isinstance(records, list) or len(roots) + len(records) > 400:
		frappe.throw("Invalid linked-record manifest")
	allowed = set()
	for owner in roots:
		if not isinstance(owner, dict) or not permitted(
			peer, owner, incoming, operation=change.get("operation") if change else None
		):
			frappe.throw("Linked owner is outside the published rule scope", frappe.PermissionError)
		allowed.add(identity(owner))
	if identity(root) not in allowed:
		frappe.throw("Linked manifest must include its authorized root", frappe.PermissionError)
	if change:
		if change.get("operation") not in ("Create", "Update", "Delete", "Rename"):
			frappe.throw("Invalid linked-record operation")
		records = [change["document"]]
	for document in records:
		if document.get("doctype") not in ASSOCIATED_TYPES or not owners(document) <= allowed:
			frappe.throw("Linked record is outside its authorized owners", frappe.PermissionError)
		prior = change.get("before") if change else None
		if prior and (identity(prior) != identity(document) or not owners(prior) <= allowed):
			frappe.throw("Invalid previous linked-record state", frappe.PermissionError)
		if not (owners(document) | owners(prior or {})) & allowed:
			frappe.throw("Linked record has no authorized relationship", frappe.PermissionError)
		authorize_document(peer, document, incoming=incoming, dependency=True)
	return allowed


def apply(peer, data, policy="Update"):
	from arab_streaming.streaming.executor import apply_document
	from arab_streaming.streaming.mapping import check_base, local_document, mapped_name, record, remember

	allowed = validate(peer, data)
	local_owners = {(dt, mapped_name(peer, dt, name)) for dt, name in allowed}
	change = data.get("related_change")
	for original in [change["document"]] if change else data.get("related_records") or []:
		document = copy.deepcopy(original)
		dt, remote_name = identity(document)
		name = mapped_name(peer, dt, remote_name)
		exists = frappe.db.exists(dt, name)
		operation = change["operation"] if change else "Create"
		if not change and exists:
			continue  # An owner save cannot overwrite a newer independent contact edit.
		before = change.get("before") if change else None
		target = frappe.get_doc(dt, name) if exists else None
		if target and operation in ("Create", "Update") and policy == "Skip":
			target.check_permission("read")
			continue
		if operation == "Rename":
			old = mapped_name(peer, dt, change.get("old_name"))
			if change.get("new_name") != remote_name or not old or change.get("merge"):
				frappe.throw("Linked record merges require an explicit reconciliation")
			if old != name and frappe.db.exists(dt, old):
				frappe.rename_doc(dt, old, name)
				target = frappe.get_doc(dt, name)
				exists = True
		if operation == "Delete":
			if target:
				if not owners(snapshot(target)) & local_owners:
					continue
				check_base(peer, original, target, digest(before or original))
				# Never delete a receiver-local/shared relationship outside this event's scope.
				other = [
					row.as_dict()
					for row in target.links
					if (row.link_doctype, row.link_name) not in local_owners
				]
				if other:
					target.set("links", other)
					target.save()
				else:
					frappe.delete_doc(dt, name)
			continue
		base = digest(before) if before else None
		if target and before:
			check_base(peer, original, target, base)
		elif target and change and operation != "Rename":
			tracked = record(peer, dt, remote_name)
			if tracked and tracked.local_digest != digest(snapshot(target)):
				frappe.throw("Linked record changed locally; reconcile this conflict before retrying")
		apply_document(peer, document, policy, preserve_related_owners=local_owners)
		remember(peer, original, frappe.get_doc(dt, name))

	# Native Address updates refresh the owner's stored address display via
	# db_set, without an owner save/event. Checkpoint only owners whose entire
	# business state now agrees with their authorized source snapshot. This
	# preserves independent local edits while tracking legitimate derived writes.
	def equivalent(value):
		if isinstance(value, dict):
			return {key: equivalent(item) for key, item in value.items()}
		if isinstance(value, list):
			return [equivalent(item) for item in value]
		return None if value == "" else value

	for owner in data.get("related_roots") or []:
		dt, remote_name = identity(owner)
		name = mapped_name(peer, dt, remote_name)
		if not frappe.db.exists(dt, name):
			continue
		target = frappe.get_doc(dt, name)
		target.check_permission("read")
		expected = local_document(peer, owner, config_for(peer, dt, incoming=True))
		if equivalent(snapshot(target)) == equivalent(expected):
			remember(peer, owner, target)
	return "Success", None
