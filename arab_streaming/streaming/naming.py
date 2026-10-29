"""Partition transaction counters by Branch and writer without changing global series defaults."""

import hashlib

import frappe

from arab_streaming.streaming.context import replay_source
from arab_streaming.streaming.policy import check_source, document_branches, stored_rules


def before_naming(doc, method=None):
	if (
		replay_source()
		or not frappe.conf.get("arab_streaming_enabled")
		or not doc.meta.is_submittable
		or not doc.meta.has_field("naming_series")
	):
		return
	rules = [r for r in stored_rules() if r.enabled and r.doctype_name == doc.doctype]
	if not rules:
		return
	check_source(doc)
	rule = next((r for r in rules if r.scope != "Shared"), None)
	if not rule:
		return
	branches = document_branches(doc, rule)
	if not branches or len(branches) != 1:
		frappe.throw("A transaction naming partition requires exactly one Branch")
	branch = next(iter(branches))
	code = hashlib.sha256(branch.encode()).hexdigest()[:6].upper()
	site = (
		frappe.db.get_value("Streaming Site State", "local", "installation_id").replace("-", "")[:6].upper()
	)
	series = doc.naming_series or doc.meta.get_field("naming_series").options.split("\n")[0]
	if series.startswith("BR-") and "-WR-" in series:
		parts = series.split("-", 4)
		series = parts[4] if len(parts) == 5 else series
	doc.naming_series = f"BR-{code}-WR-{site}-{series}"
	allow_series(doc)


def allow_series(doc, method=None):
	if (
		doc.meta.has_field("naming_series")
		and isinstance(doc.get("naming_series"), str)
		and doc.naming_series.startswith("BR-")
		and "-WR-" in doc.naming_series
	):
		field = doc.meta.get_field("naming_series")
		if doc.naming_series not in (field.options or "").split("\n"):
			field.options = (field.options or "") + "\n" + doc.naming_series


def branch_identity(doc, method=None, *args):
	if frappe.db.exists("Streaming Peer State", {"active_branch": doc.name}) or frappe.db.exists(
		"Master Instance", {"restaurant_branch": doc.name}
	):
		frappe.throw(
			"This Branch has a persistent streaming pairing. Reconcile/unlink that identity before renaming or deleting it."
		)
