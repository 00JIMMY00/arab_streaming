import frappe

from arab_streaming.streaming.adapters import SAFE_TYPES
from arab_streaming.streaming.context import replay_source


def policy_snapshot(parent_type):
	import pymysql

	connection = pymysql.connect(**frappe.db.get_connection_settings())
	try:
		with connection.cursor(pymysql.cursors.DictCursor) as cursor:
			cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
			cursor.execute(
				f"SELECT name, restaurant_branch, connection_status, receiving_doctypes_data FROM `tab{parent_type}` WHERE enabled=1"
			)
			parents = [frappe._dict(row) for row in cursor.fetchall()]
			from arab_streaming.streaming.policy import effective_rules

			for parent in parents:
				parent.doctype = parent_type
				parent.syncing_doctypes = effective_rules(parent, cursor=cursor)
				parent.incoming_rules = effective_rules(parent, incoming=True, cursor=cursor)
			cursor.execute(
				"SELECT value FROM tabSingles WHERE doctype=%s AND field=%s",
				("Streaming Settings", "strictly_validate_document_actions"),
			)
			setting = cursor.fetchone()
			return parents, int(setting["value"] or 0) if setting else 1
	finally:
		connection.rollback()
		connection.close()


def validate_action(doc, method=None, old=None, new=None, merge=False):
	if (
		doc.doctype not in SAFE_TYPES
		or replay_source() is not None
		or not frappe.conf.get("arab_streaming_enabled")
	):
		return
	role = frappe.conf.get("arab_streaming_site_role")
	if role not in ("Master", "Slave"):
		return
	parent_type = "Slave Instance" if role == "Master" else "Master Instance"
	parents, strict = policy_snapshot(parent_type)
	# Published remote restrictions are checked before local rename restrictions.
	for parent in parents:
		if parent.connection_status != "Connected" or not parent.receiving_doctypes_data or not strict:
			continue
		facts = frappe.parse_json(parent.receiving_doctypes_data)
		rows = parent.incoming_rules or facts.get("rules", [])
		for row in rows:
			if row.get("doctype_name") != doc.doctype:
				continue
			if row.get("scope"):
				from arab_streaming.streaming.policy import matches_scope

				if not matches_scope(doc, row, parent.restaurant_branch):
					continue
			if method == "before_rename" and not row.get("allow_rename"):
				frappe.throw("Rename is controlled by the connected owning site")
			if method == "on_trash":
				if row.get("prevent_delete"):
					frappe.throw("Delete is controlled by the connected owning site")
				continue
			if doc.is_new():
				if doc.meta.has_field("amended_from") and doc.get("amended_from"):
					if not row.get("allow_amend") and row.get("allow_creation"):
						frappe.throw("Amend is controlled by the connected owning site")
				elif not row.get("allow_creation"):
					frappe.throw("Creation is controlled by the connected owning site")
			elif row.get("lock_documents") or (doc.docstatus and row.get("lock_documents_after_submit")):
				frappe.throw("This document is controlled by the connected owning site")
	if method in ("on_trash", "before_rename"):
		for parent in parents:
			for row in parent.syncing_doctypes:
				from arab_streaming.streaming.policy import matches_scope

				if row.doctype_name != doc.doctype:
					continue
				if row.get("scope") and not matches_scope(doc, row, parent.restaurant_branch):
					continue
				if row.doctype_name == doc.doctype and (
					(method == "on_trash" and row.prevent_local_delete)
					or (method == "before_rename" and not row.allow_local_rename)
				):
					frappe.throw("Streaming policy prevents this local document action")
