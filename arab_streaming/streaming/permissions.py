"""App-managed, rule-scoped roles. Business execution still uses normal permissions."""

import frappe

from arab_streaming.streaming.adapters import SAFE_TYPES


def permission_types(rules):
	roots = {r["doctype_name"] for r in rules if r.get("enabled")}
	result = set(roots)
	pending = [(dt, 0) for dt in roots]
	visited = set()
	while pending:
		dt, depth = pending.pop()
		if dt in visited or depth > 4:
			continue
		visited.add(dt)
		meta = frappe.get_meta(dt)
		for field in meta.fields:
			if field.fieldtype in ("Table", "Table MultiSelect"):
				pending.append((field.options, depth))
			elif field.fieldtype == "Link" and field.options in SAFE_TYPES:
				result.add(field.options)
				pending.append((field.options, depth + 1))
	return roots, result


def provision(user, rules):
	if user in ("Administrator", "Guest") or not frappe.db.get_value("User", user, "enabled"):
		frappe.throw("Choose an enabled non-Administrator integration user")
	from frappe.permissions import setup_custom_perms

	roots, types = permission_types(rules)
	import hashlib

	role = "Arab Streaming " + hashlib.sha256(user.encode()).hexdigest()[:12]
	if not frappe.db.exists("Role", role):
		frappe.get_doc(dict(doctype="Role", role_name=role, desk_access=0)).insert(ignore_permissions=True)
	existing = frappe.get_all(
		"Custom DocPerm", filters={"role": role}, fields=["name", "parent", "permlevel"]
	)
	wanted = {}
	for dt in types:
		meta = frappe.get_meta(dt)
		levels = {0} | {int(f.permlevel or 0) for f in meta.fields if f.fieldtype != "Password"}
		for level in levels:
			wanted[(dt, level)] = dict(
				read=1,
				create=1,
				write=int(dt in roots),
				delete=int(dt in roots),
				submit=int(bool(meta.is_submittable)),
				cancel=int(bool(meta.is_submittable)),
				amend=int(bool(meta.is_submittable)),
			)
	for row in existing:
		if (row.parent, row.permlevel) not in wanted:
			frappe.db.delete("Custom DocPerm", {"name": row.name})
	for (dt, level), values in wanted.items():
		setup_custom_perms(dt)
		name = frappe.db.get_value("Custom DocPerm", dict(parent=dt, role=role, permlevel=level, if_owner=0))
		if name:
			frappe.db.set_value("Custom DocPerm", name, values)
		else:
			frappe.get_doc(
				dict(
					doctype="Custom DocPerm",
					parent=dt,
					parenttype="DocType",
					parentfield="permissions",
					role=role,
					permlevel=level,
					**values,
				)
			).insert(ignore_permissions=True)
		frappe.clear_cache(doctype=dt)
	if not frappe.db.exists("Has Role", {"parent": user, "role": role}):
		doc = frappe.get_doc("User", user)
		doc.append("roles", {"role": role})
		doc.save(ignore_permissions=True)
	frappe.clear_cache(user=user)
	return sorted(types)


def refresh_site():
	from arab_streaming.streaming.policy import stored_rules

	rules = [dict(r) for r in stored_rules() if r.enabled]
	for user in set(frappe.get_all("Streaming Peer State", pluck="execution_user")):
		if user:
			provision(user, rules)
