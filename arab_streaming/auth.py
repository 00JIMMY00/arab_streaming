"""Instance Key authentication is valid only on the explicit streaming wire API."""

import hmac
import re

import frappe
from frappe.utils.password import get_decrypted_password

WIRE_METHODS = frozenset(
	f"arab_streaming.api.{module}.{method}"
	for module, methods in {
		"connection": ("capabilities", "branches", "link", "ping", "disconnect"),
		"copy": ("source_preview", "source_start", "source_progress", "source_control"),
		"events": ("receive", "pending", "acknowledge", "results", "retry"),
		"bootstrap": (
			"count",
			"prepare",
			"page",
			"dependency",
			"finish",
			"start_copy",
			"password_values",
			"abandon",
		),
		"policies": ("published", "accept", "register_receiver"),
		"files": ("manifest", "chunk"),
		"rules": ("schema",),
		"stock": ("snapshot", "acknowledge"),
	}.items()
	for method in methods
)


def authenticate_credentials(key, secret):
	if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{15}", value) for value in (key, secret)):
		raise frappe.AuthenticationError("Invalid streaming credentials")
	row = frappe.db.get_value("Instance Key", {"api_key": key, "enabled": 1}, ["name", "user"], as_dict=True)
	stored = (
		get_decrypted_password("Instance Key", row.name, "api_secret", raise_exception=False) if row else None
	)
	if (
		not stored
		or not hmac.compare_digest(stored, secret)
		or row.user in ("Guest", "Administrator")
		or not frappe.db.get_value("User", row.user, "enabled")
	):
		raise frappe.AuthenticationError("Invalid streaming credentials")
	return row


def authenticate_request():
	header = frappe.get_request_header("Authorization", "")
	if (frappe.get_request_header("Frappe-Authorization-Source") or "").strip().lower() == "instance key":
		raise frappe.AuthenticationError("Use the dedicated streaming authentication scheme")
	if not header.lower().startswith("arabstreaming"):
		return
	frappe.flags.disable_traceback = True
	path = getattr(frappe.local, "request", None).path
	method = path.removeprefix("/api/method/") if path.startswith("/api/method/") else ""
	if method not in WIRE_METHODS or frappe.form_dict.get("cmd", method) != method:
		raise frappe.AuthenticationError("Streaming credentials are restricted to the streaming API")
	if (frappe.request.content_length or 0) > 4_194_304:
		frappe.throw("Streaming request is too large")
	parts = header.split(" ")
	if (
		len(parts) != 2
		or parts[0].lower() not in ("arabstreaming", "arabstreamingpeer")
		or parts[1].count(":") != 1
	):
		raise frappe.AuthenticationError("Invalid streaming credentials")
	if parts[0].lower() == "arabstreamingpeer":
		access, secret = parts[1].split(":")
		if not all(re.fullmatch(r"[A-Za-z0-9]{15}", x) for x in (access, secret)):
			raise frappe.AuthenticationError("Invalid peer credentials")
		name = frappe.db.get_value("Streaming Peer State", {"incoming_access_key": access})
		state = frappe.get_doc("Streaming Peer State", name) if name else None
		stored = state.get_password("incoming_secret", raise_exception=False) if state else None
		if (
			not stored
			or not hmac.compare_digest(stored, secret)
			or state.execution_user in ("Administrator", "Guest")
			or not frappe.db.get_value("User", state.execution_user, "enabled")
		):
			raise frappe.AuthenticationError("Invalid peer credentials")
		row = frappe._dict(name=name, user=state.execution_user)
		frappe.local.arab_streaming_peer = name
	else:
		row = authenticate_credentials(*parts[1].split(":"))
	form = frappe.local.form_dict
	frappe.set_user(row.user)
	frappe.local.form_dict = form
	frappe.local.arab_streaming_key = row.name


def require_wire_auth():
	key = getattr(frappe.local, "arab_streaming_key", None)
	if not key:
		raise frappe.AuthenticationError("Dedicated streaming authentication is required")
	if getattr(frappe.local, "arab_streaming_peer", None):
		state = frappe.get_doc("Streaming Peer State", frappe.local.arab_streaming_peer)
		return frappe._dict(
			api_key=state.incoming_access_key, user=state.execution_user, peer_state=state.name
		)
	return frappe.get_doc("Instance Key", key)
