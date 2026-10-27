import frappe

ROLE_KEY = "arab_streaming_site_role"
ROLES = ("Master", "Slave")


def get_site_role():
	role = frappe.conf.get(ROLE_KEY)
	if role in (None, ""):
		return None
	if role not in ROLES:
		frappe.throw(
			"arab_streaming_site_role must be Master or Slave; remove it to leave streaming unconfigured"
		)
	return role


def require_role(expected):
	if get_site_role() != expected:
		frappe.throw(f"This action requires an explicitly configured {expected} site")
