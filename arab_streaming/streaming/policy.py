"""Versioned dynamic routing. Branch scope is authorization, not a UI filter."""

import json
import re
from contextlib import contextmanager

import frappe
import pymysql
from frappe.utils import cint

from arab_streaming.streaming.guards import system_write, system_write_active

RULE_FIELDS = (
	"event_create",
	"event_update",
	"event_submit",
	"event_cancel",
	"event_rename",
	"event_delete",
	"separate_reverse_condition",
	"reverse_conditions",
	"doctype_name",
	"enabled",
	"direction",
	"scope",
	"all_branches",
	"branch_field",
	"conditions",
	"priority",
	"on_duplicated_insert",
	"sync_events_after_submit",
	"allow_creation",
	"lock_documents",
	"lock_documents_after_submit",
	"allow_amend",
	"allow_rename",
	"prevent_delete",
	"allow_local_rename",
	"prevent_local_delete",
	"include_files",
	"include_passwords",
	"revision",
)


@contextmanager
def committed():
	connection = pymysql.connect(**frappe.db.get_connection_settings())
	try:
		with connection.cursor(pymysql.cursors.DictCursor) as cursor:
			cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
			yield cursor
	finally:
		connection.rollback()
		connection.close()


def payload(doc):
	return dict(
		name=doc.name,
		**{key: doc.get(key) for key in RULE_FIELDS},
		branches=[row.branch for row in doc.branches],
		field_mappings=[
			dict(source_field=row.source_field, target_field=row.target_field) for row in doc.field_mappings
		],
	)


def stored_rules(cursor=None):
	if not frappe.db.exists("DocType", "Streaming Rule"):
		return []

	def read(cur):
		cur.execute("SELECT * FROM `tabStreaming Rule` ORDER BY priority,name")
		rows = [frappe._dict(r) for r in cur.fetchall()]
		by_name = {r.name: r for r in rows}
		for r in rows:
			r.branches = []
			r.field_mappings = []
		cur.execute(
			"SELECT parent,branch FROM `tabStreaming Rule Branch` WHERE parenttype='Streaming Rule' ORDER BY idx"
		)
		for child in cur.fetchall():
			if child["parent"] in by_name:
				by_name[child["parent"]].branches.append(child["branch"])
		cur.execute(
			"SELECT parent,source_field,target_field FROM `tabStreaming Field Mapping` WHERE parenttype='Streaming Rule' ORDER BY idx"
		)
		for child in cur.fetchall():
			if child["parent"] in by_name:
				by_name[child["parent"]].field_mappings.append(
					dict(source_field=child["source_field"], target_field=child["target_field"])
				)
		return rows

	if cursor:
		if not hasattr(cursor, "_arab_streaming_rules"):
			cursor._arab_streaming_rules = read(cursor)
		return cursor._arab_streaming_rules
	with committed() as cur:
		return read(cur)


def direction_allowed(direction, incoming=False):
	role = frappe.conf.get("arab_streaming_site_role")
	send_master = (role == "Master") != incoming
	return direction in ("Both", "Master to Slave" if send_master else "Slave to Master")


def effective_rules(peer, incoming=False, cursor=None):
	branch = peer.restaurant_branch
	rows = [row for row in stored_rules(cursor) if row.all_branches or branch in row.branches]
	selected = {row.doctype_name for row in rows}
	result = [
		frappe._dict(row.copy()) for row in rows if row.enabled and direction_allowed(row.direction, incoming)
	]
	for row in result:
		if frappe.conf.get("arab_streaming_site_role") == "Master" and incoming:
			row["field_mappings"] = [
				dict(source_field=m["target_field"], target_field=m["source_field"])
				for m in row.get("field_mappings") or []
			]
		if (frappe.conf.get("arab_streaming_site_role") == "Slave") != incoming and row.get(
			"separate_reverse_condition"
		):
			row["conditions"] = row.get("reverse_conditions") or ""
	result = [frappe._dict(row) for row in result]
	# Historical tables remain readable for a non-migrated pairing. A central
	# rule, including Disabled, takes precedence over that exact legacy scope.
	if cursor:
		cursor.execute(
			"SELECT * FROM `tabInstance Syncing Doctype` WHERE parent=%s AND parenttype=%s AND parentfield=%s ORDER BY idx",
			(peer.name, peer.doctype, "syncing_doctypes"),
		)
		legacy = cursor.fetchall()
	else:
		legacy = peer.get("syncing_doctypes") or []
	if not incoming:
		for item in legacy:
			row = frappe._dict(item.as_dict() if hasattr(item, "as_dict") else item)
			if row.doctype_name not in selected:
				row.name = row.get("rule_id") or "legacy:" + row.name
				row.revision = row.get("rule_revision") or 0
				row.scope = row.get("scope") or (
					"Branch" if discover_branch_field(row.doctype_name) else "Shared"
				)
				row.branch_field = row.get("branch_field") or discover_branch_field(row.doctype_name)
				row.include_files = 1
				result.append(row)
	return result


def config_for(peer, doctype, incoming=False, cursor=None):
	rows = [r for r in effective_rules(peer, incoming, cursor) if r.doctype_name == doctype]
	if len(rows) > 1:
		frappe.throw("Overlapping Streaming Rules for this DocType and Branch")
	return rows[0] if rows else None


def discover_branch_field(doctype):
	if not frappe.db.exists("DocType", doctype):
		return None
	meta = frappe.get_meta(doctype)
	for key in ("branch", "custom_streaming_branch", "custom_branch", "restaurant_branch"):
		f = meta.get_field(key)
		if f and f.fieldtype == "Link" and f.options == "Branch":
			return key
	return next((f.fieldname for f in meta.fields if f.fieldtype == "Link" and f.options == "Branch"), None)


def _values(document, parts):
	if not parts:
		return []
	meta = frappe.get_meta(document.get("doctype") if isinstance(document, dict) else document.doctype)
	field = meta.get_field(parts[0])
	if not field:
		frappe.throw("Configured Branch field is absent from this site")
	value = document.get(parts[0])
	if len(parts) == 1:
		if field.fieldtype != "Link" or field.options != "Branch":
			frappe.throw("Branch resolver must end at a Link to Branch")
		return [value] if value else []
	if field.fieldtype in ("Table", "Table MultiSelect"):
		return [v for child in value or [] for v in _values(child, parts[1:])]
	if field.fieldtype == "Link" and value:
		linked = frappe.get_doc(field.options, value)
		linked.check_permission("read")
		return _values(linked, parts[1:])
	return []


def document_branches(document, config):
	if config.get("scope") == "Shared":
		return None
	if config.get("scope") == "Assigned Branches" and not config.get("branch_field"):
		name = frappe.db.get_value(
			"Streaming Branch Assignment",
			{"reference_doctype": document.get("doctype"), "reference_name": document.get("name")},
		)
		return (
			set(
				frappe.get_all(
					"Streaming Rule Branch",
					filters={"parent": name, "parenttype": "Streaming Branch Assignment"},
					pluck="branch",
				)
			)
			if name
			else set()
		)
	path = config.get("branch_field") or discover_branch_field(
		config.get("doctype_name") or document.get("doctype")
	)
	if not path or not re.fullmatch(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*){0,2}", path):
		frappe.throw("Choose a valid Branch field for this Streaming Rule")
	return set(_values(document, path.split(".")))


def matches_scope(document, config, branch, required=False):
	values = document_branches(document, config)
	if values is None:
		return True
	if not values:
		if required:
			frappe.throw("A Branch is required before synchronizing this document")
		return False
	if config.get("scope") != "Assigned Branches" and len(values) != 1:
		frappe.throw("Cross-branch documents need an explicit central workflow; they cannot be broadcast")
	return branch in values


def check_source(doc):
	if not frappe.db.exists("DocType", "Streaming Rule"):
		return
	rows = [r for r in stored_rules() if r.enabled and r.doctype_name == doc.doctype]
	if not rows:
		return
	branch = None
	if frappe.conf.get("arab_streaming_site_role") == "Slave":
		branch = frappe.db.get_value("Master Instance", {"enabled": 1}, "restaurant_branch")
	for row in rows:
		if row.scope == "Shared":
			continue
		path = row.branch_field or discover_branch_field(doc.doctype)
		if branch and path and "." not in path and not doc.get(path):
			doc.set(path, branch)
		values = document_branches(doc, row)
		if not values:
			frappe.throw("Select the Branch before saving this synchronized document")
		if branch and (values != {branch}):
			frappe.throw(
				"This branch site cannot write records belonging to another Branch", frappe.PermissionError
			)
		old = doc.get_doc_before_save()
		if old and document_branches(old, row) != values:
			frappe.throw(
				"Branch cannot change after the synchronized record was saved; use an explicit transfer workflow"
			)


def authorize_document(peer, document, incoming=False, config=None, dependency=False):
	config = config or config_for(peer, document["doctype"], incoming)
	if not config and dependency:
		# Shared references remain global when a Branch field is added for UI consistency.
		reverse = config_for(peer, document["doctype"], not incoming)
		if reverse and reverse.get("scope") == "Shared":
			config = reverse
	if not config:
		if dependency:
			path = discover_branch_field(document["doctype"])
			if not path:
				return
			config = frappe._dict(doctype_name=document["doctype"], scope="Branch", branch_field=path)
		else:
			frappe.throw(
				"This DocType/direction is not enabled for the authenticated Branch", frappe.PermissionError
			)
	if not matches_scope(document, config, peer.restaurant_branch):
		frappe.throw("Document Branch does not match the authenticated peer", frappe.PermissionError)


def ensure_dimension():
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
		make_dimension_in_accounting_doctypes,
	)
	from frappe.custom.doctype.custom_field.custom_field import create_custom_field

	name = frappe.db.get_value("Accounting Dimension", {"document_type": "Branch"})
	if name:
		doc = frappe.get_doc("Accounting Dimension", name)
	else:
		doc = frappe.get_doc(
			dict(doctype="Accounting Dimension", document_type="Branch", label="Branch", fieldname="branch")
		).insert(ignore_permissions=True)
	make_dimension_in_accounting_doctypes(doc)
	inventory = frappe.db.get_value("Inventory Dimension", {"reference_document": "Branch"})
	if not inventory:
		frappe.get_doc(
			dict(
				doctype="Inventory Dimension",
				dimension_name="Branch",
				reference_document="Branch",
				source_fieldname=doc.fieldname,
				target_fieldname=doc.fieldname,
				apply_to_all_doctypes=1,
				fetch_from_parent=doc.fieldname,
				validate_negative_stock=0,
			)
		).insert(ignore_permissions=True)
	frappe.clear_cache()
	return doc.fieldname


def ensure_branch_field(doctype):
	from frappe.custom.doctype.custom_field.custom_field import create_custom_field

	from arab_streaming.streaming.adapters import require_supported

	meta = require_supported(doctype)
	existing = discover_branch_field(doctype)
	if existing:
		return existing
	create_custom_field(
		doctype,
		dict(
			fieldname="custom_streaming_branch",
			label="Branch",
			fieldtype="Link",
			options="Branch",
			insert_after=next(
				(
					f.fieldname
					for f in meta.fields
					if f.fieldtype not in ("Tab Break", "Section Break", "Column Break")
				),
				None,
			),
		),
		ignore_validate=True,
	)
	frappe.clear_cache(doctype=doctype)
	return "custom_streaming_branch"


def validate_rule(doc):
	from arab_streaming.protocol.codec import digest
	from arab_streaming.streaming.adapters import condition_fields, require_supported
	from arab_streaming.streaming.conditions import parse

	if frappe.conf.get("arab_streaming_site_role") == "Slave" and not system_write_active():
		frappe.throw(
			"Rules are published by the master. Configure direction and branches on the master.",
			frappe.PermissionError,
		)
	meta = require_supported(doc.doctype_name)
	parse(doc.conditions, condition_fields(doc.doctype_name))
	if doc.separate_reverse_condition:
		parse(doc.reverse_conditions, condition_fields(doc.doctype_name))
	if doc.scope == "Branch":
		doc.branch_field = doc.branch_field or discover_branch_field(doc.doctype_name)
		if doc.enabled and not doc.branch_field:
			frappe.throw("Use Set Up Branch Field before enabling this Branch-scoped rule")
	if meta.issingle and (doc.scope != "Shared" or doc.direction != "Master to Slave"):
		frappe.throw("Business singleton settings use Shared scope and Master to Slave direction")
	if doc.scope == "Shared" and meta.is_submittable:
		frappe.throw("Transactions must use a Branch scope; Shared is for reference data")
	if not doc.all_branches and not doc.branches:
		frappe.throw("Select participating branches or enable All Branches")
	if len({r.branch for r in doc.branches}) != len(doc.branches):
		frappe.throw("A branch can appear only once")
	for mapping in doc.field_mappings:
		if not meta.has_field(mapping.source_field):
			frappe.throw("Source field mapping is not in the selected DocType")
	# Serialize policy edits against business export boundaries and concurrent rules.
	frappe.db.sql("SELECT name FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE")
	for other in frappe.get_all(
		"Streaming Rule",
		filters={"doctype_name": doc.doctype_name, "name": ["!=", doc.name or ""]},
		fields=["name", "all_branches", "enabled"],
	):
		if not other.enabled or not doc.enabled:
			continue
		branches = set(
			frappe.get_all(
				"Streaming Rule Branch",
				filters={"parent": other.name, "parenttype": "Streaming Rule"},
				pluck="branch",
			)
		)
		if doc.all_branches or other.all_branches or branches.intersection(r.branch for r in doc.branches):
			frappe.throw("Use one Streaming Rule per DocType and Branch; select Both for both directions")
	previous = doc.get_doc_before_save()
	if frappe.conf.get("arab_streaming_site_role") != "Slave":
		doc.revision = (cint(previous.revision) + 1) if previous else 1
	item = payload(doc)
	item.pop("name", None)
	doc.configuration_hash = digest(item)


def record_revision(doc):
	if not frappe.db.exists("Streaming Rule Revision", {"rule": doc.name, "revision": doc.revision}):
		with system_write():
			frappe.get_doc(
				dict(
					doctype="Streaming Rule Revision",
					rule=doc.name,
					revision=doc.revision,
					configuration=json.dumps(payload(doc)),
					configuration_hash=doc.configuration_hash,
				)
			).insert(ignore_permissions=True)
	if frappe.conf.get("arab_streaming_site_role") == "Master":
		from arab_streaming.streaming.permissions import provision

		users = set(
			frappe.get_all(
				"Streaming Peer State", filters={"instance_type": "Slave Instance"}, pluck="execution_user"
			)
		)
		rules = [
			payload(frappe.get_doc("Streaming Rule", name))
			for name in frappe.get_all("Streaming Rule", filters={"enabled": 1}, pluck="name")
		]
		for user in users:
			if user:
				provision(user, rules)
		from arab_streaming.tasks import wakeup

		frappe.db.after_commit.add(wakeup)


def migrate_legacy_rules():
	if (
		not frappe.db.exists("DocType", "Streaming Rule")
		or frappe.conf.get("arab_streaming_site_role") != "Master"
	):
		return

	def own_rules(peer):
		# Migration must see rules inserted earlier in this same transaction.
		result = []
		for name in frappe.get_all("Streaming Rule", pluck="name"):
			doc = frappe.get_doc("Streaming Rule", name)
			if doc.all_branches or peer.restaurant_branch in [r.branch for r in doc.branches]:
				result.append(doc)
		return result

	for peername in frappe.get_all("Slave Instance", pluck="name"):
		peer = frappe.get_doc("Slave Instance", peername)
		for row in peer.syncing_doctypes:
			identity = "Slave Instance:" + peer.name + ":" + row.name
			if frappe.db.exists("Streaming Rule", {"legacy_source": identity}):
				continue
			if any(
				r.doctype_name == row.doctype_name
				for r in own_rules(peer)
				if not r.name.startswith("legacy:")
			):
				continue
			data = {k: row.get(k) for k in RULE_FIELDS if row.meta.has_field(k)}
			data.update(
				doctype="Streaming Rule",
				doctype_name=row.doctype_name,
				enabled=1,
				direction="Master to Slave",
				all_branches=0,
				branches=[{"branch": peer.restaurant_branch}],
				scope=row.get("scope") or ("Branch" if discover_branch_field(row.doctype_name) else "Shared"),
				legacy_source=identity,
				include_files=1,
			)
			with system_write():
				frappe.get_doc(data).insert(ignore_permissions=True)
	# Reverse legacy facts are migrated without making the slave a policy authority.
	for peername in frappe.get_all("Slave Instance", pluck="name"):
		peer = frappe.get_doc("Slave Instance", peername)
		facts = frappe.parse_json(peer.receiving_doctypes_data or "{}")
		for row in facts.get("rules", []):
			if row.get("revision"):
				continue
			matches = [
				r
				for r in own_rules(peer)
				if r.doctype_name == row.get("doctype_name") and not r.name.startswith("legacy:")
			]
			# Combine only identical conditions; preserve differences as an explicit migration warning.
			if matches:
				old = frappe.get_doc("Streaming Rule", matches[0].name)
				if (old.legacy_source or "").endswith("|reverse") or (old.legacy_source or "").startswith(
					"Reverse:"
				):
					continue
				if old.direction == "Both" and (old.reverse_conditions or old.conditions or "") == (
					row.get("conditions") or ""
				):
					continue
				old.direction = "Both"
				old.legacy_source = (old.legacy_source or "") + "|reverse"
				if (old.conditions or "") != (row.get("conditions") or ""):
					old.separate_reverse_condition = 1
					old.reverse_conditions = row.get("conditions") or ""
				with system_write():
					old.save(ignore_permissions=True)
				continue
			data = {k: row.get(k) for k in RULE_FIELDS if k in row}
			data.update(
				doctype="Streaming Rule",
				enabled=1,
				direction="Slave to Master",
				all_branches=0,
				branches=[{"branch": peer.restaurant_branch}],
				scope="Branch" if discover_branch_field(row["doctype_name"]) else "Shared",
				include_files=1,
				legacy_source="Reverse:" + peer.name + ":" + row["doctype_name"],
			)
			with system_write():
				frappe.get_doc(data).insert(ignore_permissions=True)


def migrate_rule_contract():
	if frappe.conf.get("arab_streaming_site_role") != "Master":
		return
	for name in frappe.get_all("Streaming Rule", pluck="name"):
		doc = frappe.get_doc("Streaming Rule", name)
		revision = frappe.db.get_value(
			"Streaming Rule Revision", {"rule": name, "revision": doc.revision}, "configuration"
		)
		old = frappe.parse_json(revision or "{}")
		if "event_create" not in old:
			for operation in ("create", "update", "submit", "cancel", "rename", "delete"):
				doc.set("event_" + operation, 1)
			with system_write():
				doc.save(ignore_permissions=True)


def validate_captured_configuration(config):
	"""A branch cannot invent the master's historical execution policy."""
	if (
		frappe.conf.get("arab_streaming_site_role") != "Master"
		or not config.get("rule_id")
		or config["rule_id"].startswith("legacy:")
	):
		return
	data = frappe.db.get_value(
		"Streaming Rule Revision",
		{"rule": config["rule_id"], "revision": config.get("rule_revision")},
		"configuration",
	)
	if not data:
		frappe.throw(
			"Unknown published rule revision; Test Configuration before retrying", frappe.PermissionError
		)
	expected = frappe.parse_json(data)
	if expected.get("separate_reverse_condition"):
		expected["conditions"] = expected.get("reverse_conditions") or ""
	for key in RULE_FIELDS:
		if key in ("all_branches", "revision", "enabled"):
			continue
		left, right = config.get(key), expected.get(key)
		if left in (None, ""):
			left = None
		if right in (None, ""):
			right = None
		if left != right:
			frappe.throw(
				"Captured execution policy differs from its published revision: " + key,
				frappe.PermissionError,
			)
	if (config.get("field_mappings") or []) != (expected.get("field_mappings") or []):
		frappe.throw("Captured field mappings differ from the published revision", frappe.PermissionError)
