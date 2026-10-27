"""Explicit defaults for new connections. Migrations never reset saved rows."""

import frappe

INITIAL_FIELDS = frozenset(
	{
		"doctype_name",
		"conditions",
		"sync_from",
		"mandatory",
		"ignore_links",
		"skip_if_exists",
		"skip_links_if_exists",
		"ignore_branch",
	}
)
SYNCING_FIELDS = frozenset(
	{
		"doctype_name",
		"priority",
		"conditions",
		"directly_inserted_in_db",
		"on_duplicated_insert",
		"ignore_validation",
		"lock_documents",
		"allow_creation",
		"sync_events_after_submit",
		"lock_documents_after_submit",
		"allow_amend",
		"allow_rename",
		"prevent_delete",
		"ignore_branch",
		"prevent_local_delete",
		"allow_local_rename",
	}
)


def get_default_rows(kind, role):
	if kind not in ("initial", "syncing") or role not in ("Master", "Slave"):
		frappe.throw("Invalid streaming default scope")
	# Only initial foundation suggestions; no ongoing business rule is enabled.
	rows = (
		[{"doctype_name": "UOM", "mandatory": 1}, {"doctype_name": "Item Group", "mandatory": 1}]
		if kind == "initial" and role == "Slave"
		else []
	)
	for provider in frappe.get_hooks("arab_streaming_default_providers"):
		contribution = frappe.get_attr(provider)(kind=kind, role=role)
		if not isinstance(contribution, list):
			frappe.throw("Streaming default providers must return a list of rows")
		rows.extend(contribution)
	allowed = INITIAL_FIELDS if kind == "initial" else SYNCING_FIELDS
	seen = set()
	for row in rows:
		if not isinstance(row, dict) or not row.keys() <= allowed or not row.get("doctype_name"):
			frappe.throw("Invalid streaming default row")
		if row["doctype_name"] in seen:
			frappe.throw("Streaming default providers contributed the same DocType twice")
		if not frappe.db.exists("DocType", row["doctype_name"]):
			frappe.throw("Streaming defaults reference an unavailable DocType")
		seen.add(row["doctype_name"])
	return [dict(row) for row in rows]
