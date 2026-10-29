import hashlib
from datetime import date, datetime, time
from decimal import Decimal

import frappe

from arab_streaming.protocol.envelope import canonical_json
from arab_streaming.streaming.adapters import transferable_fields

EXCLUDE = frozenset(
	{
		"creation",
		"set_posting_time",
		"modified",
		"owner",
		"modified_by",
		"parent",
		"parenttype",
		"parentfield",
		"idx",
		"lft",
		"rgt",
		"old_parent",
		"_user_tags",
		"_comments",
		"_assign",
		"_liked_by",
		"_seen",
	}
)


def value(item):
	if isinstance(item, (datetime, date, time)):
		return item.isoformat()
	if isinstance(item, Decimal):
		return str(item)
	if isinstance(item, list):
		return [value(child) for child in item]
	if isinstance(item, dict):
		return {key: value(child) for key, child in item.items()}
	return item


def snapshot(doc):
	result = {"doctype": doc.doctype, "name": doc.name, "docstatus": int(doc.docstatus)}
	for field in transferable_fields(doc.meta):
		name = field.fieldname
		if field.fieldtype in ("Table", "Table MultiSelect"):
			result[name] = [snapshot(child) for child in doc.get(name) or []]
		else:
			item = doc.get(name)
			# Frappe writes empty numeric fields as zero and reloads decimal
			# fields as floats. Hash the stored representation on both sides.
			if field.fieldtype in ("Check", "Int", "Long Int"):
				result[name] = frappe.utils.cint(item)
			elif field.fieldtype in ("Float", "Currency", "Percent", "Duration", "Rating"):
				result[name] = frappe.utils.flt(item)
			elif field.fieldtype == "Time":
				result[name] = (
					frappe.utils.get_time(item).isoformat() if item is not None and item != "" else None
				)
			elif field.fieldtype == "Date":
				result[name] = frappe.utils.getdate(item).isoformat() if item else None
			elif field.fieldtype == "Datetime":
				result[name] = frappe.utils.get_datetime(item).isoformat() if item else None
			else:
				result[name] = value(item)
	return result


def digest(document):
	return hashlib.sha256(canonical_json(document)).hexdigest()
