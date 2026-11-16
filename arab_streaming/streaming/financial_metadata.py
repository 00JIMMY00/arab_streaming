"""Narrow native db_set metadata updates after a fully validated financial save."""

import frappe

FIELDS = {"Sales Invoice": ("status",), "POS Invoice": ("status", "consolidated_invoice")}


def stage(target, document):
	if not target or target.docstatus != 1 or document.get("docstatus") != 1:
		return {}
	changes = {}
	for name in FIELDS.get(target.doctype, ()):
		if name not in document or document[name] == target.get(name):
			continue
		value = document[name]
		field = target.meta.get_field(name)
		if name == "status" and value not in (field.options or "").split("\n"):
			frappe.throw("Invalid source financial status")
		if name == "consolidated_invoice" and value:
			invoice = frappe.get_doc("Sales Invoice", value)
			invoice.check_permission("read")
			if invoice.company != target.company or not any(
				row.get("pos_invoice") == target.name for row in invoice.items
			):
				frappe.throw("Consolidated invoice does not contain this POS Invoice")
		changes[name] = value
		document[name] = target.get(name)
	return changes


def apply(target, changes):
	if changes:
		target.db_set(changes, update_modified=False)
