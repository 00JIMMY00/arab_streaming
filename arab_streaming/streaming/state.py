import frappe

from arab_streaming.streaming.configuration import require_role
from arab_streaming.streaming.guards import require_system_write

MAX_SEQUENCE = 2**63 - 1


def next_sequence():
	"""Hold the site row lock until caller commit; rollback also rolls back allocation."""
	require_system_write()
	rows = frappe.db.sql(
		"SELECT event_sequence FROM `tabStreaming Site State` WHERE name=%s FOR UPDATE", ("local",)
	)
	if not rows:
		frappe.throw("Streaming site state is missing; run the site migration")
	value = int(rows[0][0]) + 1
	if value > MAX_SEQUENCE:
		frappe.throw("Streaming sequence exhausted; processing has stopped")
	frappe.db.set_value("Streaming Site State", "local", "event_sequence", value, update_modified=False)
	return value
