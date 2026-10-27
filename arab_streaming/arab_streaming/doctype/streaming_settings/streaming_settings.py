import math

import frappe
from frappe.model.document import Document


def effective_retention_days(value):
	if value is None or value == "":
		return 60
	try:
		valid = not isinstance(value, bool) and int(value) == float(value) and int(value) >= 0
	except (TypeError, ValueError, OverflowError):
		valid = False
	if not valid:
		frappe.throw("Clear Events After must be a non-negative whole number")
	return int(value) or 60


class StreamingSettings(Document):
	def validate(self):
		effective_retention_days(self.clear_events_after_days)
		# Frappe v15 can load singleton Duration values as strings.
		age = frappe.utils.flt(self.stock_snapshot_max_age)
		if self.strict_stock_snapshot_validation and (not math.isfinite(age) or age < 600):
			frappe.throw("Stock Snapshot Max Age must be at least 600 seconds in strict mode")
