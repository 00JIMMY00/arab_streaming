"""Installed apps can offer business targets while retaining the wire scope identity."""

import frappe


def provider(operation, **values):
	handlers = frappe.get_hooks("arab_streaming_connection_target_providers")
	if len(handlers) > 1:
		frappe.throw("Only one connection target provider can be active")
	return frappe.get_attr(handlers[0])(operation, **values) if handlers else None


def descriptor():
	return provider("descriptor")


def describe(document):
	return provider("describe", document=document)


def load_view(document, method=None):
	view = describe(document)
	if view:
		document.set_onload("connection_target", view)


def choices():
	result = provider("choices")
	if result is not None:
		return result
	assigned = frappe.get_all(
		"Streaming Peer State", filters={"active_branch": ["is", "set"]}, pluck="active_branch"
	)
	rows = frappe.get_list(
		"Branch", filters={"custom_streaming_enabled": 1}, fields=["name"], limit_page_length=1000
	)
	return {
		"doctype": "Branch",
		"label": "Branch",
		"targets": [{"name": row.name, "label": row.name} for row in rows if row.name not in assigned],
	}


def resolve(target):
	if not isinstance(target, str) or not target or len(target) > 140:
		frappe.throw("Choose a valid connection target")
	result = provider("resolve", target=target)
	if result is not None:
		return result
	if target not in {row["name"] for row in choices()["targets"]}:
		frappe.throw("Choose an available connection target", frappe.PermissionError)
	return {"branch": target}


def eligible_targets(name):
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import master_connection

	doc = master_connection(name)
	return remote_call(doc, "arab_streaming.api.connection.targets")


def link_target(name, target):
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import link_connection, master_connection

	doc = master_connection(name)
	if doc.slave_name:
		current = describe(doc)
		if (current.get("name") if current else doc.restaurant_branch) != target:
			frappe.throw("An existing connection cannot be moved to another target")
		return link_connection(name, doc.restaurant_branch)
	resolved = remote_call(doc, "arab_streaming.api.connection.resolve_target", target=target)
	if not isinstance(resolved.get("branch"), str) or not resolved["branch"]:
		frappe.throw("The master did not resolve this connection target")
	return link_connection(name, resolved["branch"])
