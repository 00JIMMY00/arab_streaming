import frappe

from arab_streaming.streaming.configuration import require_role

BRANCH_DOCTYPE = "Branch"
ENABLED_FIELD = "custom_streaming_enabled"


def get_enabled_branches():
	"""Internal provider; ordinary Branch permissions and User Permissions apply."""
	require_role("Master")
	return frappe.get_list(BRANCH_DOCTYPE, filters={ENABLED_FIELD: 1}, fields=["name"], order_by="name")
