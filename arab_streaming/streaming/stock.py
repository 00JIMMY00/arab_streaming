"""Reconcile Branch ledger movements; never independently replicate Bin/SLE/GL.

Basis: sum of immutable ERPNext SLE actual_qty and stock_value_difference for
selected voucher types and this Branch, grouped by item and warehouse. This is
movement reconciliation, not an invented replacement opening warehouse balance.
A safe watermark requires durable receipt/application and no outgoing backlog.
"""

import json
from datetime import datetime, timezone

import frappe

from arab_streaming.protocol.codec import digest
from arab_streaming.streaming.connections import peer_state, wire_identity
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.policy import discover_branch_field, effective_rules

STOCK_TYPES = frozenset(
	{
		"Stock Entry",
		"Stock Reconciliation",
		"Delivery Note",
		"Purchase Receipt",
		"Sales Invoice",
		"Purchase Invoice",
		"Subcontracting Receipt",
	}
)


def kinds(peer, incoming=False):
	return sorted({r.doctype_name for r in effective_rules(peer, incoming) if r.doctype_name in STOCK_TYPES})


def selected_vouchers(peer, types):
	from arab_streaming.streaming.adapters import condition_fields
	from arab_streaming.streaming.conditions import filters, parse
	from arab_streaming.streaming.policy import matches_scope

	result = {}
	for rule in effective_rules(peer):
		if rule.doctype_name not in types:
			continue
		predicate = filters(parse(rule.conditions, condition_fields(rule.doctype_name)))
		path = rule.branch_field or discover_branch_field(rule.doctype_name)
		if path and "." not in path:
			predicate.append([path, "=", peer.restaurant_branch])
		names = frappe.get_list(rule.doctype_name, filters=predicate, pluck="name", limit_page_length=5001)
		if len(names) > 5000:
			frappe.throw("Stock reconciliation scope exceeds 5000 vouchers; narrow the rule conditions")
		result[rule.doctype_name] = [
			name
			for name in names
			if matches_scope(frappe.get_doc(rule.doctype_name, name), rule, peer.restaurant_branch)
		]
	return result


def movements(peer, types, vouchers):
	branch_field = discover_branch_field("Stock Ledger Entry")
	if not branch_field:
		frappe.throw("Branch Inventory Dimension is missing from Stock Ledger Entry")
	rows = []
	for kind in types:
		names = vouchers.get(kind) or []
		if not names:
			continue
		rows.extend(
			frappe.db.sql(
				f"""SELECT item_code,warehouse,SUM(actual_qty) quantity,SUM(stock_value_difference) value
            FROM `tabStock Ledger Entry` WHERE `{branch_field}`=%s AND voucher_type=%s AND voucher_no IN %s
            GROUP BY item_code,warehouse""",
				(peer.restaurant_branch, kind, tuple(names)),
				as_dict=True,
			)
		)
	totals = {}
	for row in rows:
		key = (row.item_code, row.warehouse)
		quantity, value = totals.get(key, (0, 0))
		totals[key] = (quantity + float(row.quantity or 0), value + float(row.value or 0))
	return sorted(
		[
			dict(item=key[0], warehouse=key[1], quantity=round(qty, 6), value=round(value, 6))
			for key, (qty, value) in totals.items()
			if abs(qty) > 1e-7 or abs(value) > 1e-5
		],
		key=lambda r: (r["item"], r["warehouse"]),
	)


def validate_freshness(snapshot, maximum_age, now=None):
	try:
		captured = datetime.fromisoformat(snapshot["captured_at"])
		if captured.tzinfo is None:
			raise ValueError
		age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
	except (ValueError, KeyError, TypeError):
		frappe.throw("Stock snapshot has no authoritative UTC timestamp")
	if age < -60:
		frappe.throw("Stock snapshot timestamp is in the future; correct the site clocks")
	if age > maximum_age:
		frappe.throw("Stock snapshot is stale; reconnect and request a fresh reconciliation")
	if (
		not isinstance(snapshot.get("source_sequence"), str)
		or not snapshot["source_sequence"].isdecimal()
		or int(snapshot["source_sequence"]) >= 2**63
	):
		frappe.throw("Invalid stock reconciliation sequence")
	if snapshot.get("payload_digest") != digest(snapshot.get("data")):
		frappe.throw("Stock snapshot digest mismatch")


def produce(peer, state):

	types = kinds(peer)
	if not types:
		return {"not_applicable": True}
	frappe.db.commit()
	head = frappe.db.sql(
		"SELECT event_sequence FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE"
	)[0][0]
	vouchers = selected_vouchers(peer, types)
	data = dict(
		branch=peer.restaurant_branch,
		types=types,
		vouchers=vouchers,
		movements=movements(peer, types, vouchers),
		basis="branch-ledger-movements-v1",
	)
	at = datetime.now(timezone.utc).isoformat()
	from arab_streaming.streaming.exports import outstanding

	pending = outstanding(peer, head)
	with system_write():
		doc = frappe.get_doc(
			dict(
				doctype="Streaming Stock Snapshot",
				peer_state=state.name,
				source_sequence=head,
				captured_at=at,
				data=json.dumps(data),
				payload_digest=digest(data),
				direction="Source",
				status="Pending",
			)
		).insert(ignore_permissions=True)
	return dict(
		snapshot=doc.name,
		source_sequence=str(head),
		captured_at=at,
		data=data,
		payload_digest=doc.payload_digest,
		outstanding=pending,
	)


def acknowledge(peer, state, name, checksum):
	doc = frappe.get_doc("Streaming Stock Snapshot", name, for_update=True)
	if doc.peer_state != state.name or doc.direction != "Source" or doc.payload_digest != checksum:
		frappe.throw("Stock acknowledgement is not for this paired snapshot", frappe.PermissionError)
	maximum = float(frappe.db.get_single_value("Streaming Settings", "stock_snapshot_max_age") or 900)
	validate_freshness(
		dict(
			source_sequence=str(doc.source_sequence),
			captured_at=doc.captured_at,
			data=frappe.parse_json(doc.data),
			payload_digest=checksum,
		),
		maximum,
	)
	with system_write():
		doc.status = "Reconciled"
		doc.save(ignore_permissions=True)
		state.stock_status = "Reconciled"
		state.stock_reconciled_at = frappe.utils.now_datetime()
		state.stock_acknowledged_through = max(state.stock_acknowledged_through or 0, doc.source_sequence)
		state.save(ignore_permissions=True)
	return {"status": "Reconciled"}


def reconcile(peer, force=False):
	state = peer_state(peer.doctype, peer.name)
	if not kinds(peer, incoming=True):
		return
	if (
		not force
		and state.stock_checked_at
		and (frappe.utils.now_datetime() - state.stock_checked_at).total_seconds() < 30
	):
		return
	from arab_streaming.streaming.client import remote_call

	result = remote_call(peer, "arab_streaming.api.stock.snapshot", **wire_identity(peer))
	if result.get("not_applicable"):
		return
	status, error = "Pending", None
	try:
		maximum = float(frappe.db.get_single_value("Streaming Settings", "stock_snapshot_max_age") or 900)
		validate_freshness(result, maximum)
		data = result["data"]
		if (
			data.get("branch") != peer.restaurant_branch
			or data.get("basis") != "branch-ledger-movements-v1"
			or set(data.get("types") or []) != set(kinds(peer, incoming=True))
		):
			frappe.throw("Stock snapshot scope differs from the published Branch rules")
		head = int(result["source_sequence"])
		if head < max(state.received_through or 0, state.observed_source_head or 0):
			frappe.throw("Stock source sequence moved backwards; reconcile a possible restore")
		if (
			result["outstanding"]
			or frappe.db.exists(
				"Capture Event",
				{
					"instance_type": peer.doctype,
					"instance_name": peer.name,
					"event_sequence": ["<=", head],
					"status": ["in", ["Pending", "Failed"]],
					"ignore": 0,
				},
			)
			or frappe.db.exists(
				"Dispatch Event Instance",
				{"instance_type": peer.doctype, "instance_name": peer.name, "synced": 0, "ignore": 0},
			)
		):
			error = "Waiting for outstanding delivery/application; this snapshot is not reconciled"
		else:
			from arab_streaming.streaming.mapping import mapped_name

			expected = []
			for row in data["movements"]:
				row = dict(
					row,
					item=mapped_name(peer, "Item", row["item"]),
					warehouse=mapped_name(peer, "Warehouse", row["warehouse"]),
				)
				expected.append(row)
			expected = sorted(expected, key=lambda r: (r["item"], r["warehouse"]))
			vouchers = {
				kind: [mapped_name(peer, kind, name) for name in names]
				for kind, names in data.get("vouchers", {}).items()
			}
			missing = any(
				not frappe.db.exists(kind, name) for kind, names in vouchers.items() for name in names
			)
			if missing or expected != movements(peer, data["types"], vouchers):
				status, error = (
					"Conflict",
					"Branch stock movements differ. Review missing/failed transactions and Item/Warehouse mappings; balances were not overwritten.",
				)
			else:
				status = "Reconciled"
	except Exception as exc:
		status, error = "Stale", str(exc)[:1500]
	with system_write():
		frappe.get_doc(
			dict(
				doctype="Streaming Stock Snapshot",
				peer_state=state.name,
				source_sequence=int(result["source_sequence"]),
				captured_at=result["captured_at"],
				data=json.dumps(result["data"]),
				payload_digest=result["payload_digest"],
				direction="Received",
				status=status,
				exception=error,
			)
		).insert(ignore_permissions=True)
		state.stock_checked_at = frappe.utils.now_datetime()
		state.stock_status = status
		state.stock_exception = error
		if status == "Reconciled":
			state.stock_reconciled_at = frappe.utils.now_datetime()
			state.stock_received_safe = max(state.stock_received_safe or 0, int(result["source_sequence"]))
		state.save(ignore_permissions=True)
	frappe.db.commit()
	if status == "Reconciled":
		remote_call(
			peer,
			"arab_streaming.api.stock.acknowledge",
			**wire_identity(peer),
			snapshot=result["snapshot"],
			checksum=result["payload_digest"],
		)
	return dict(status=status, error=error)


def validate_action(doc):
	if not frappe.conf.get("arab_streaming_advanced_stock_checks"):
		return
	if doc.doctype not in STOCK_TYPES or getattr(doc, "_action", None) != "submit":
		return
	if doc.doctype in ("Sales Invoice", "Purchase Invoice") and not doc.get("update_stock"):
		return
	if not frappe.db.get_single_value("Streaming Settings", "strict_stock_snapshot_validation"):
		return
	from arab_streaming.streaming.policy import matches_scope

	parent = "Slave Instance" if frappe.conf.arab_streaming_site_role == "Master" else "Master Instance"
	for name in frappe.get_all(parent, filters={"enabled": 1}, pluck="name"):
		peer = frappe.get_doc(parent, name)
		rules = [r for r in effective_rules(peer) if r.doctype_name == doc.doctype]
		if not rules or not matches_scope(doc, rules[0], peer.restaurant_branch):
			continue
		state = peer_state(parent, name)
		age = (
			(frappe.utils.now_datetime() - state.stock_reconciled_at).total_seconds()
			if state.stock_reconciled_at
			else float("inf")
		)
		maximum = float(frappe.db.get_single_value("Streaming Settings", "stock_snapshot_max_age") or 900)
		if age > maximum or state.stock_status in ("Conflict", "Stale") or not state.stock_reconciled_at:
			frappe.throw(
				"Strict stock validation requires a fresh reconciled Branch snapshot. Open this connection → Reconcile Stock, resolve its stock diagnostic, then submit. Max age: "
				+ str(int(maximum))
				+ " seconds."
			)


def safe_to_remove(event):
	if event.reference_doctype not in STOCK_TYPES:
		return True
	if event.doctype == "Dispatch Event":
		for row in event.instances:
			state = peer_state(row.instance_type, row.instance_name)
			if (state.stock_acknowledged_through or 0) < event.event_sequence:
				return False
	else:
		state = peer_state(event.instance_type, event.instance_name)
		if (state.stock_received_safe or 0) < event.event_sequence:
			return False
	return True
