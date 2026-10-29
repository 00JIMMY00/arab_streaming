"""Isolated-site HTTPS acceptance helpers; never callable through the web API."""

import json
from pathlib import Path

import frappe

from arab_streaming.streaming.guards import system_write
from arab_streaming.tests.finance_helpers import USER, setup

CUSTOM = "AST Business Record"
CHILD = "AST Business Row"


def prepare(role):
	if not frappe.local.site.endswith(".test"):
		raise RuntimeError("Isolated acceptance sites only")
	setup()
	frappe.set_user("Administrator")
	frappe.conf.arab_streaming_site_role = role
	frappe.conf.arab_streaming_enabled = False
	if not frappe.db.exists("DocType", CHILD):
		frappe.get_doc(
			dict(
				doctype="DocType",
				name=CHILD,
				module="Selling",
				custom=1,
				istable=1,
				fields=[
					dict(fieldname="value", label="Value", fieldtype="Data"),
					dict(fieldname="amount", label="Amount", fieldtype="Currency"),
				],
			)
		).insert()
	if not frappe.db.exists("DocType", CUSTOM):
		frappe.get_doc(
			dict(
				doctype="DocType",
				name=CUSTOM,
				module="Selling",
				custom=1,
				autoname="field:title",
				allow_rename=1,
				fields=[
					dict(fieldname="title", label="Title", fieldtype="Data", reqd=1),
					dict(fieldname="branch", label="Branch", fieldtype="Link", options="Branch"),
					dict(fieldname="value", label="Value", fieldtype="Data"),
					dict(fieldname="attachment", label="Attachment", fieldtype="Attach"),
					dict(fieldname="secret", label="Secret", fieldtype="Password"),
					dict(fieldname="lines", label="Lines", fieldtype="Table", options=CHILD),
				],
				permissions=[dict(role="System Manager", read=1, write=1, create=1, delete=1)],
			)
		).insert()
	if role == "Master":
		if not frappe.db.exists("Instance Key", {"user": USER}):
			key = frappe.get_doc(dict(doctype="Instance Key", user=USER, enabled=1)).insert()
		else:
			key = frappe.get_doc("Instance Key", frappe.db.get_value("Instance Key", {"user": USER}))
		credentials = Path("/home/xxx/bench1/arab-streaming-test/services/acceptance-credentials.json")
		credentials.write_text(
			json.dumps(dict(access_key=key.api_key, secret=key.get_password("api_secret")))
		)
		credentials.chmod(0o600)
		for dt, scope in [
			(CUSTOM, "Branch"),
			("Sales Invoice", "Branch"),
			("Sales Order", "Branch"),
			("UOM", "Shared"),
		]:
			if not frappe.db.exists("Streaming Rule", {"doctype_name": dt}):
				frappe.get_doc(
					dict(
						doctype="Streaming Rule",
						doctype_name=dt,
						enabled=1,
						direction="Both",
						scope=scope,
						branch_field="branch" if scope == "Branch" else None,
						all_branches=1,
					)
				).insert()
	else:
		credentials = json.loads(
			Path("/home/xxx/bench1/arab-streaming-test/services/acceptance-credentials.json").read_text()
		)
		if not frappe.db.exists("Master Instance"):
			with system_write():
				doc = frappe.get_doc(
					dict(
						doctype="Master Instance",
						domain="https://127.0.0.1:13322",
						site_domain="https://127.0.0.1:13323",
						execution_user=USER,
						enabled=1,
						**credentials,
					)
				).insert(ignore_permissions=True)
		else:
			doc = frappe.get_doc("Master Instance", frappe.get_all("Master Instance", pluck="name")[0])
		frappe.db.commit()
		from arab_streaming.streaming.connections import link_connection

		link_connection(doc.name, "_AST Branch 01")
	frappe.db.commit()
	from frappe.installer import update_site_config

	update_site_config("arab_streaming_site_role", role)
	update_site_config("arab_streaming_enabled", True)
	update_site_config(
		"arab_streaming_public_url", f"https://127.0.0.1:{13322 if role == 'Master' else 13323}"
	)
	return dict(role=role, custom=CUSTOM)


def health():
	return dict(
		rules=frappe.get_all("Streaming Rule", fields=["doctype_name", "enabled", "revision"]),
		peers=frappe.get_all(
			"Streaming Peer State",
			fields=[
				"instance_type",
				"instance_name",
				"policy_hash",
				"agent_heartbeat",
				"last_delivery_error",
			],
		),
		captures=frappe.get_all(
			"Capture Event",
			filters={"status": ["in", ["Pending", "Failed"]]},
			fields=["reference_doctype", "reference_name", "status", "exception"],
			limit_page_length=10,
		),
	)


def debug_failure():
	from arab_streaming.streaming.executor import apply_capture

	names = frappe.get_all(
		"Capture Event",
		filters={"status": "Failed", "reference_doctype": CUSTOM},
		pluck="name",
		order_by="creation desc",
		limit_page_length=1,
	)
	if not names:
		return {}
	capture = frappe.get_doc("Capture Event", names[0])
	peer = frappe.get_doc(capture.instance_type, capture.instance_name)
	try:
		return apply_capture(capture, peer)
	except Exception:
		return frappe.get_traceback()


def permission_health():
	return dict(
		roles=frappe.get_roles(USER),
		perms=frappe.get_all(
			"Custom DocPerm", filters={"parent": CUSTOM}, fields=["role", "read", "write", "create"]
		),
		read=frappe.has_permission(CUSTOM, "read", user=USER),
	)


def repair_permissions():
	from arab_streaming.streaming.permissions import provision
	from arab_streaming.streaming.policy import stored_rules

	result = provision(USER, [dict(r) for r in stored_rules() if r.enabled])
	frappe.db.commit()
	return {"types": len(result), "read": frappe.has_permission(CUSTOM, "read", user=USER)}


def latest_document():
	name = frappe.get_all(CUSTOM, pluck="name", order_by="creation desc", limit_page_length=1)[0]
	doc = frappe.get_doc(CUSTOM, name)
	return dict(
		name=doc.name,
		value=doc.value,
		lines=[dict(name=r.name, value=r.value, amount=r.amount) for r in doc.lines],
		captures=frappe.get_all(
			"Capture Event", filters={"reference_name": name}, fields=["status", "data", "exception"]
		),
	)


def ensure_pricing():
	if not frappe.local.site.endswith(".test"):
		raise RuntimeError("Isolated sites only")
	if not frappe.db.exists("Price List", "Standard Selling"):
		frappe.get_doc(
			dict(
				doctype="Price List",
				price_list_name="Standard Selling",
				selling=1,
				buying=0,
				currency="USD",
				enabled=1,
			)
		).insert()
	frappe.db.commit()
	return {"pricing": "ready"}


def retire_matrix_peers():
	if frappe.local.site != "streaming-master.test":
		raise RuntimeError("Isolated master only")
	with system_write():
		for name in frappe.get_all("Slave Instance", filters={"api_key": "acceptance"}, pluck="name"):
			doc = frappe.get_doc("Slave Instance", name)
			doc.enabled = 0
			doc.connection_status = "Disconnected"
			doc.save(ignore_permissions=True)
			state = frappe.get_doc(
				"Streaming Peer State", frappe.db.get_value("Streaming Peer State", {"instance_name": name})
			)
			state.active_branch = None
			state.save(ignore_permissions=True)
	frappe.db.commit()
	return {"fixture_peers": "retired without readdressing history"}


def link_load_branch(number):
	if frappe.local.site != f"streaming-branch{int(number):02d}.test":
		raise RuntimeError("Isolated branch only")
	from frappe.installer import update_site_config

	number = int(number)
	credentials = json.loads(
		Path("/home/xxx/bench1/arab-streaming-test/services/acceptance-credentials.json").read_text()
	)
	with system_write():
		doc = frappe.get_doc(
			dict(
				doctype="Master Instance",
				domain="https://127.0.0.1:13322",
				site_domain=f"https://127.0.0.1:{13322 + number}",
				execution_user=USER,
				enabled=1,
				**credentials,
			)
		).insert(ignore_permissions=True)
	frappe.db.commit()
	from arab_streaming.streaming.connections import link_connection

	result = link_connection(doc.name, f"_AST Branch {number:02d}")
	frappe.db.commit()
	update_site_config("arab_streaming_enabled", True)
	return result
