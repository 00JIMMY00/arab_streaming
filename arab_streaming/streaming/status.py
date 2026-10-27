def dispatch_status(destinations):
	"""Receipt state is independent of receiving business application."""
	rows = list(destinations)
	for row in rows:
		if row.get("synced") and row.get("ignore"):
			raise ValueError("A synced destination cannot also be ignored")
	if not rows:
		return "Pending"
	if all(row.get("synced") for row in rows):
		return "Synced"
	if all(row.get("ignore") for row in rows):
		return "Ignored"
	if any(row.get("synced") for row in rows):
		return "Partially Synced"
	if any(row.get("ignore") for row in rows):
		return "Partially Ignored"
	return "Pending"
