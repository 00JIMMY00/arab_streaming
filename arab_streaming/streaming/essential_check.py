"""Small named operator smoke checks, callable only from the server console."""

import frappe

from arab_streaming import __version__


def create_uom(name):
	frappe.only_for("System Manager")
	if not isinstance(name, str) or not name.startswith("Arab Streaming Verify "):
		frappe.throw("Use a named streaming verification UOM")
	if not frappe.db.exists("UOM", name):
		frappe.get_doc(dict(doctype="UOM", uom_name=name, must_be_whole_number=0)).insert()
	else:
		frappe.get_doc("UOM", name).save()
	frappe.db.commit()
	return dict(
		uom=name,
		dispatches=frappe.get_all(
			"Dispatch Event",
			filters={"reference_doctype": "UOM", "reference_name": name},
			fields=["name", "status", "event_type", "event_sequence"],
		),
	)


def inspect(uom=None):
	from arab_streaming.streaming.policy import stored_rules

	return dict(
		version=__version__,
		role=frappe.conf.get("arab_streaming_site_role"),
		uom_exists=bool(frappe.db.exists("UOM", uom)) if uom else None,
		rules=[
			dict(
				name=r.name,
				doctype=r.doctype_name,
				direction=r.direction,
				scope=r.scope,
				conditions=r.conditions,
				reverse_conditions=r.reverse_conditions,
				enabled=r.enabled,
			)
			for r in stored_rules()
		],
		peer=frappe.get_all(
			"Streaming Peer State",
			fields=[
				"instance_type",
				"instance_name",
				"agent_heartbeat",
				"last_delivery_error",
				"policy_hash",
			],
		),
		captures=frappe.get_all(
			"Capture Event",
			filters={"reference_name": uom},
			fields=["name", "status", "exception", "remote_dispatch_event"],
		)
		if uom
		else [],
	)


def configure_test_rules(branch):
	frappe.only_for("System Manager")
	if branch != "Arab-ERP Streaming Test Branch" or frappe.conf.get("arab_streaming_site_role") != "Master":
		frappe.throw("This check is limited to the approved test branch")
	result = []
	for doctype, scope in [("UOM", "Shared"), ("Sales Invoice", "Branch")]:
		name = frappe.db.get_value("Streaming Rule", {"doctype_name": doctype})
		doc = (
			frappe.get_doc("Streaming Rule", name)
			if name
			else frappe.get_doc(dict(doctype="Streaming Rule", doctype_name=doctype))
		)
		doc.enabled = 1
		doc.direction = "Both"
		doc.scope = scope
		doc.branch_field = "branch" if scope == "Branch" else None
		doc.all_branches = 0
		doc.branches = []
		doc.append("branches", {"branch": branch})
		doc.conditions = ""
		doc.separate_reverse_condition = 0
		doc.reverse_conditions = ""
		doc.save()
		result.append(dict(name=doc.name, doctype=doctype, direction=doc.direction, scope=scope))
	frappe.db.commit()
	return result
