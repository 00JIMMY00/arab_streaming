def dispatch_status(destinations):
	"""Only confirmed receiver application is green; receipt alone is Delivered."""
	rows = list(destinations)
	for row in rows:
		if row.get("synced") and row.get("ignore"):
			raise ValueError("A delivered destination cannot also be ignored")
	if not rows:
		return "Pending"
	if any(row.get("receiver_status") == "Failed" for row in rows if row.get("synced")):
		return "Failed"
	if any(row.get("exception") for row in rows if not row.get("synced") and not row.get("ignore")):
		return "Delivery Failed"
	if all(row.get("synced") and row.get("receiver_status") == "Success" for row in rows):
		return "Applied"
	if all(row.get("ignore") or row.get("receiver_status") == "Ignored" for row in rows):
		return "Ignored"
	if all(row.get("synced") and row.get("receiver_status") == "Skipped" for row in rows):
		return "Skipped"
	if all(row.get("synced") and row.get("receiver_status") in ("Success", "Skipped") for row in rows):
		return "Completed with Skips"
	if any(row.get("synced") and row.get("receiver_status") in ("Success", "Skipped") for row in rows):
		return "Partially Applied"
	if all(row.get("synced") for row in rows):
		return "Delivered"
	if any(row.get("synced") for row in rows):
		return "Partially Delivered"
	if any(row.get("ignore") for row in rows):
		return "Partially Ignored"
	return "Pending"
