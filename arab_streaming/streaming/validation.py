import json
from urllib.parse import urlsplit, urlunsplit

import frappe


def validate_json_object(value):
	if value is None or value == "":
		return {}
	try:
		parsed = json.loads(value) if isinstance(value, str) else value
	except (ValueError, TypeError):
		frappe.throw("Streaming metadata must be a JSON object")
	if not isinstance(parsed, dict):
		frappe.throw("Streaming metadata must be a JSON object")
	return parsed


def normalize_site_url(value):
	value = (value or "").strip()
	if not value or any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
		frappe.throw("Enter a valid HTTPS site URL")
	if "://" not in value:
		value = "https://" + value
	try:
		url = urlsplit(value)
		port = url.port
		if (
			url.scheme != "https"
			or not url.hostname
			or url.username is not None
			or url.password is not None
			or url.query
			or url.fragment
			or url.path not in ("", "/")
		):
			raise ValueError
		host = url.hostname.encode("idna").decode("ascii").lower()
		if ":" in host:
			host = f"[{host}]"
		if port and port != 443:
			host += f":{port}"
		return urlunsplit(("https", host, "", "", ""))
	except (ValueError, UnicodeError):
		frappe.throw("Enter an HTTPS site URL without credentials, path, query or fragment")


def validate_rules(doc):
	# Executable conditions remain disabled until the shared restricted parser is implemented.
	for table in ("initial_sync_doctypes", "syncing_doctypes"):
		seen = set()
		for row in doc.get(table) or []:
			if row.doctype_name in seen:
				frappe.throw("A DocType can appear only once in each streaming configuration table")
			seen.add(row.doctype_name)
			if table == "syncing_doctypes" and (row.priority or 0) < 0:
				frappe.throw("Streaming priority cannot be negative")
