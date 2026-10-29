import frappe

from arab_streaming.streaming.adapters import condition_fields
from arab_streaming.streaming.conditions import matches, parse
from arab_streaming.streaming.policy import config_for, discover_branch_field, matches_scope


def scoped_config(peer, row):
	config = config_for(peer, row["doctype_name"])
	if config:
		return config
	path = discover_branch_field(row["doctype_name"])
	if frappe.get_meta(row["doctype_name"]).is_submittable and not path:
		frappe.throw("Set up the Branch dimension before copying transactions")
	return frappe._dict(
		doctype_name=row["doctype_name"],
		scope="Branch" if path else "Shared",
		branch_field=path,
		conditions="",
	)


def selected_names(peer, row):
	from arab_streaming.streaming.exports import selection

	config = scoped_config(peer, row)
	if frappe.get_meta(row["doctype_name"]).issingle:
		doc = frappe.get_single(row["doctype_name"])
		doc.check_permission("read")
		if matches_scope(doc, config, peer.restaurant_branch) and matches(
			parse(row.get("conditions"), condition_fields(doc.doctype)), doc
		):
			yield doc.doctype
		return
	names = frappe.get_list(
		row["doctype_name"], filters=selection(row), pluck="name", order_by="name", limit_page_length=10001
	)
	for name in names:
		doc = frappe.get_doc(row["doctype_name"], name)
		doc.check_permission("read")
		if matches_scope(doc, config, peer.restaurant_branch) and matches(
			parse(config.get("conditions"), condition_fields(doc.doctype)), doc
		):
			yield name
