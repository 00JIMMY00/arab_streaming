"""Selected business Password bootstrap; never export encryption keys or sessions."""

import json

import frappe
from frappe.utils.password import get_decrypted_password

from arab_streaming.protocol.codec import digest
from arab_streaming.streaming.guards import system_write


def freeze(document):
	if document["doctype"] == "User":
		return None
	values = []

	def walk(doc):
		for field in frappe.get_meta(doc["doctype"]).fields:
			if field.fieldtype in ("Table", "Table MultiSelect"):
				for child in doc.get(field.fieldname) or []:
					walk(child)
			elif field.fieldtype == "Password":
				value = get_decrypted_password(
					doc["doctype"], doc["name"], field.fieldname, raise_exception=False
				)
				if value is not None:
					values.append(
						dict(doctype=doc["doctype"], name=doc["name"], field=field.fieldname, value=value)
					)

	walk(document)
	if not values:
		return None
	with system_write():
		return (
			frappe.get_doc(
				dict(
					doctype="Streaming Protected Version",
					reference_doctype=document["doctype"],
					reference_name=document["name"],
					document_digest=digest(document),
					secret_data=json.dumps(values, ensure_ascii=False),
				)
			)
			.insert(ignore_permissions=True)
			.name
		)


def fetch(peer, state, export, doctype, name):
	from arab_streaming.streaming.exports import authorized_export
	from arab_streaming.streaming.policy import authorize_document, config_for

	authorized_export(state, export)
	row = frappe.db.get_value(
		"Streaming Snapshot Item",
		{"export": export, "reference_doctype": doctype, "reference_name": name},
		["data", "protected_version"],
		as_dict=True,
	)
	if not row or not row.protected_version:
		return {"values": []}
	rule = config_for(peer, doctype)
	if not rule or not rule.include_passwords:
		frappe.throw("Password bootstrap is not enabled for this rule", frappe.PermissionError)
	document = frappe.parse_json(row.data)
	authorize_document(peer, document)
	if not frappe.has_permission(doctype, "read"):
		frappe.throw("No permission to bootstrap this business record", frappe.PermissionError)
	protected = frappe.get_doc("Streaming Protected Version", row.protected_version)
	if (protected.reference_doctype, protected.reference_name, protected.document_digest) != (
		doctype,
		name,
		digest(document),
	):
		frappe.throw("Protected bootstrap version does not match its immutable document")
	with system_write():
		return {"values": json.loads(protected.get_password("secret_data"))}


def import_values(peer, document, export):
	if document["doctype"] == "User":
		return []
	if not export:
		return []
	from arab_streaming.streaming.policy import config_for

	rule = config_for(peer, document["doctype"], incoming=True)
	if not rule or not rule.include_passwords:
		return []
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import wire_identity

	data = remote_call(
		peer,
		"arab_streaming.api.bootstrap.password_values",
		**wire_identity(peer),
		export=export,
		doctype=document["doctype"],
		name=document["name"],
	)
	allowed = {}

	def walk(doc):
		for field in frappe.get_meta(doc["doctype"]).fields:
			if field.fieldtype in ("Table", "Table MultiSelect"):
				for child in doc.get(field.fieldname) or []:
					walk(child)
			elif field.fieldtype == "Password":
				allowed[(doc["doctype"], doc["name"], field.fieldname)] = True

	walk(document)
	values = data.get("values")
	if not isinstance(values, list) or len(values) > 1000:
		frappe.throw("Invalid protected bootstrap data")
	for value in values:
		if (
			not isinstance(value, dict)
			or (value.get("doctype"), value.get("name"), value.get("field")) not in allowed
			or not isinstance(value.get("value"), str)
		):
			frappe.throw("Protected bootstrap field is not part of this document")
	return values


def apply(doc, values):
	by_identity = {(doc.doctype, doc.name): doc}
	for child in doc.get_all_children():
		by_identity[(child.doctype, child.name)] = child
	for value in values:
		target = by_identity.get((value["doctype"], value["name"]))
		if not target:
			frappe.throw("Password bootstrap child identity mismatch")
		target.set(value["field"], value["value"])
