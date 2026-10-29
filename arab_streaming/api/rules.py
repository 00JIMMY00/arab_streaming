import frappe

from arab_streaming.protocol.codec import digest
from arab_streaming.streaming.adapters import require_supported
from arab_streaming.streaming.policy import ensure_branch_field, ensure_dimension, payload


@frappe.whitelist()
def describe(doctype):
	frappe.only_for("System Manager")
	meta = require_supported(doctype)
	from arab_streaming.streaming.policy import discover_branch_field

	return dict(
		doctype=doctype,
		is_submittable=bool(meta.is_submittable),
		branch_field=discover_branch_field(doctype),
		scope="Branch" if meta.is_submittable or discover_branch_field(doctype) else "Shared",
	)


@frappe.whitelist(methods=["POST"])
def setup_branch(doctype=None):
	frappe.only_for("System Manager")
	if doctype:
		return {"branch_field": ensure_branch_field(doctype)}
	return {"branch_field": ensure_dimension()}


@frappe.whitelist(methods=["POST"])
def test_configuration(name):
	frappe.only_for("System Manager")
	rule = frappe.get_doc("Streaming Rule", name)
	rule.check_permission("read")
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import peer_state, wire_identity
	from arab_streaming.streaming.delivery import synchronize_policy
	from arab_streaming.streaming.permissions import provision
	from arab_streaming.streaming.policy import stored_rules

	results = []
	for peername in frappe.get_all("Slave Instance", filters={"enabled": 1}, pluck="name"):
		peer = frappe.get_doc("Slave Instance", peername)
		if not rule.all_branches and peer.restaurant_branch not in [r.branch for r in rule.branches]:
			continue
		try:
			state = peer_state(peer.doctype, peer.name)
			provision(state.execution_user, [dict(r) for r in stored_rules() if r.enabled])
			frappe.db.commit()
			synchronize_policy(peer, force=True)
			data = remote_call(
				peer, "arab_streaming.api.rules.schema", **wire_identity(peer), doctype=rule.doctype_name
			)
			local = schema_contract(rule.doctype_name)
			mapping = {m.source_field: m.target_field for m in rule.field_mappings}
			missing = [f for f in local["fields"] if mapping.get(f, f) not in data["fields"]]
			incompatible = [
				f
				for f, contract in local["fields"].items()
				if mapping.get(f, f) in data["fields"]
				and contract[:2] != data["fields"][mapping.get(f, f)][:2]
			]
			if incompatible:
				frappe.throw("Receiving field types/options differ: " + ", ".join(incompatible[:10]))
			extra_required = [
				f
				for f, contract in data["fields"].items()
				if contract[2] and f not in {mapping.get(k, k) for k in local["fields"]}
			]
			if extra_required:
				frappe.throw("Receiving required fields need a mapping: " + ", ".join(extra_required[:10]))
			if local["children"] != data.get("children"):
				frappe.throw(
					"Child table schemas differ; install the same customizations before synchronizing"
				)
			if missing:
				frappe.throw("Receiving schema is missing fields: " + ", ".join(missing[:10]))
			results.append(dict(branch=peer.restaurant_branch, status="Ready", schema=data["schema_hash"]))
		except Exception as exc:
			frappe.db.rollback()
			results.append(dict(branch=peer.restaurant_branch, status="Needs Setup", error=str(exc)[:1000]))
	status = "Ready" if results and all(r["status"] == "Ready" for r in results) else "Needs Setup"
	frappe.db.set_value("Streaming Rule", rule.name, "configuration_status", status)
	return {"status": status, "branches": results}


def schema_contract(doctype):
	from arab_streaming.streaming.adapters import transferable_fields

	meta = require_supported(doctype)
	fields = {f.fieldname: [f.fieldtype, f.options, int(f.reqd or 0)] for f in transferable_fields(meta)}
	children = {
		f.options: {
			c.fieldname: [c.fieldtype, c.options, int(c.reqd or 0)]
			for c in transferable_fields(frappe.get_meta(f.options))
		}
		for f in transferable_fields(meta)
		if f.fieldtype in ("Table", "Table MultiSelect")
	}
	return dict(
		doctype=doctype,
		fields=fields,
		children=children,
		is_submittable=bool(meta.is_submittable),
		schema_hash=digest({"fields": fields, "children": children}),
	)


@frappe.whitelist(methods=["POST"])
def schema(protocol, version, pairing_id, source_installation_id, pairing_generation, doctype):
	from arab_streaming.api.events import addressed
	from arab_streaming.streaming.policy import config_for

	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	if not config_for(peer, doctype, True) and not config_for(peer, doctype):
		frappe.throw("No rule for this DocType/Branch", frappe.PermissionError)
	if not frappe.has_permission(doctype, "read", user=state.execution_user):
		frappe.throw("Integration role has no read permission", frappe.PermissionError)
	return schema_contract(doctype)


@frappe.whitelist()
def connection_summary(doctype, name):
	frappe.only_for("System Manager")
	if doctype not in ("Master Instance", "Slave Instance"):
		frappe.throw("Invalid connection")
	peer = frappe.get_doc(doctype, name)
	peer.check_permission("read")
	from arab_streaming.streaming.connections import peer_state
	from arab_streaming.streaming.policy import stored_rules

	state = peer_state(doctype, name)
	return dict(
		rules=[
			dict(
				name=r.name,
				doctype_name=r.doctype_name,
				direction=r.direction,
				scope=r.scope,
				enabled=r.enabled,
			)
			for r in stored_rules()
			if r.all_branches or peer.restaurant_branch in r.branches
		],
		outstanding=frappe.db.count(
			"Dispatch Event Instance",
			{"instance_type": doctype, "instance_name": name, "synced": 0, "ignore": 0},
		),
		pending=frappe.db.count(
			"Capture Event",
			{
				"instance_type": doctype,
				"instance_name": name,
				"status": ["in", ["Pending", "Failed"]],
				"ignore": 0,
			},
		),
		error=state.last_delivery_error or str(state.policy_errors or ""),
		heartbeat=state.agent_heartbeat,
	)


@frappe.whitelist(methods=["POST"])
def copy_existing(name):
	frappe.only_for("System Manager")
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.configuration import require_role
	from arab_streaming.streaming.connections import wire_identity

	require_role("Master")
	rule = frappe.get_doc("Streaming Rule", name)
	rule.check_permission("read")
	if not rule.enabled or rule.direction == "Slave to Master":
		frappe.throw("Enable a Master to Slave or Both rule before copying master records")
	result = []
	for peername in frappe.get_all(
		"Slave Instance", filters={"enabled": 1, "connection_status": "Connected"}, pluck="name"
	):
		peer = frappe.get_doc("Slave Instance", peername)
		if not rule.all_branches and peer.restaurant_branch not in [r.branch for r in rule.branches]:
			continue
		try:
			from arab_streaming.streaming.delivery import synchronize_policy

			synchronize_policy(peer, force=True)
			data = remote_call(
				peer,
				"arab_streaming.api.bootstrap.start_copy",
				**wire_identity(peer),
				doctypes=[rule.doctype_name],
			)
			result.append(dict(branch=peer.restaurant_branch, **data))
		except Exception as exc:
			result.append(dict(branch=peer.restaurant_branch, error=str(exc)[:1000]))
	return {"branches": result}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def doctype_query(doctype, txt, searchfield, start, page_len, filters):
	frappe.only_for("System Manager")
	from arab_streaming.streaming.adapters import is_business_doctype

	candidates = frappe.get_all(
		"DocType",
		filters={"istable": 0, "name": ["like", "%" + txt + "%"]},
		pluck="name",
		order_by="name",
		limit_page_length=10000,
	)
	# Frappe searches translated DocTypes with blank txt and pages only after
	# matching translations. Truncating that candidate set hides later names.
	limit = 25000 if frappe.get_meta(doctype).translated_doctype else 50
	return [[name] for name in candidates if is_business_doctype(name)][
		int(start) : int(start) + min(int(page_len), limit)
	]
