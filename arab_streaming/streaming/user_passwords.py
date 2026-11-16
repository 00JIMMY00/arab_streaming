"""Opt-in, addressed User password-hash events; never publish credential material."""

import hashlib
import json

import frappe

from arab_streaming.streaming.context import replay_source
from arab_streaming.streaming.guards import system_write


def eligible(user):
	return bool(
		user
		and user not in ("Administrator", "Guest")
		and not user.endswith(("@rms.invalid", "@arab-erp.invalid"))
	)


def last_fingerprint(user):
	return frappe.db.get_value(
		"Streaming Protected Version",
		{"reference_doctype": "User", "reference_name": user},
		"document_digest",
		order_by="creation desc",
	)


def capture(doc, method=None):
	if (
		not eligible(doc.name)
		or replay_source()
		or not frappe.conf.get("arab_streaming_enabled")
		or frappe.conf.get("arab_streaming_site_role") != "Master"
	):
		return
	from arab_streaming.protocol.codec import snapshot
	from arab_streaming.streaming.outbox import fence, recipients, store_event

	fence(doc)
	selected = [item for item in recipients(doc, "on_update") if item[2].get("include_passwords")]
	if not selected:
		return
	rows = frappe.db.sql(
		"SELECT password FROM `__Auth` WHERE doctype='User' AND name=%s AND fieldname='password' AND encrypted=0",
		doc.name,
	)
	if not rows:
		return
	hashed = rows[0][0]
	fingerprint = hashlib.sha256(hashed.encode()).hexdigest()
	if last_fingerprint(doc.name) == fingerprint:
		return
	with system_write():
		version = frappe.get_doc(
			dict(
				doctype="Streaming Protected Version",
				reference_doctype="User",
				reference_name=doc.name,
				document_digest=fingerprint,
				secret_data=json.dumps({"hash": hashed}),
			)
		).insert(ignore_permissions=True)
	return store_event(
		doc,
		"Update",
		selected,
		data={"document": snapshot(doc), "user_password_version": version.name},
		method="password_changed",
	)


def scan(limit=20):
	if frappe.conf.get("arab_streaming_site_role") != "Master" or not frappe.conf.get(
		"arab_streaming_enabled"
	):
		return
	from arab_streaming.streaming.policy import stored_rules

	if not any(r.enabled and r.doctype_name == "User" and r.include_passwords for r in stored_rules()):
		return
	# Compare hashes in-process; no hashes are returned to the operator or event JSON.
	count = 0
	for row in frappe.db.sql(
		"SELECT a.name,a.password FROM `__Auth` a JOIN `tabUser` u ON u.name=a.name WHERE a.doctype='User' AND a.fieldname='password' AND a.encrypted=0 ORDER BY a.name LIMIT 10001",
		as_dict=True,
	):
		if not eligible(row.name):
			continue
		fingerprint = hashlib.sha256(row.password.encode()).hexdigest()
		if last_fingerprint(row.name) == fingerprint:
			continue
		if capture(frappe.get_doc("User", row.name)):
			count += 1
		if count >= limit:
			break


def fetch(peer, event_name, version_name):
	if peer.doctype != "Slave Instance" or frappe.conf.get("arab_streaming_site_role") != "Master":
		frappe.throw(
			"Only a paired Slave may fetch an addressed master password change", frappe.PermissionError
		)
	event = frappe.get_doc("Dispatch Event", event_name)
	data = frappe.parse_json(event.data)
	if (
		event.reference_doctype != "User"
		or not eligible(event.reference_name)
		or data.get("user_password_version") != version_name
		or not any(r.instance_type == peer.doctype and r.instance_name == peer.name for r in event.instances)
	):
		frappe.throw("Password version is not addressed to this peer", frappe.PermissionError)
	from arab_streaming.streaming.policy import authorize_document, config_for

	config = config_for(peer, "User")
	if not config or not config.include_passwords:
		frappe.throw("User password synchronization is disabled", frappe.PermissionError)
	authorize_document(peer, data["document"])
	frappe.get_doc("User", event.reference_name).check_permission("read")
	version = frappe.get_doc("Streaming Protected Version", version_name)
	if version.reference_doctype != "User" or version.reference_name != event.reference_name:
		frappe.throw("Password identity mismatch", frappe.PermissionError)
	return {"hash": json.loads(version.get_password("secret_data"))["hash"]}


def apply(peer, capture_doc, data):
	version = data.get("user_password_version")
	if not version:
		return
	if (
		peer.doctype != "Master Instance"
		or frappe.conf.get("arab_streaming_site_role") != "Slave"
		or capture_doc.reference_doctype != "User"
	):
		frappe.throw("Password changes are accepted only from the paired Master", frappe.PermissionError)
	from arab_streaming.streaming.policy import config_for

	config = config_for(peer, "User", incoming=True)
	if not config or not config.include_passwords:
		frappe.throw("User password synchronization is disabled", frappe.PermissionError)
	from arab_streaming.streaming.mapping import mapped_name

	user = mapped_name(peer, "User", capture_doc.reference_name)
	if not eligible(user):
		frappe.throw("Protected identities cannot receive passwords", frappe.PermissionError)
	frappe.get_doc("User", user).check_permission("write")
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import wire_identity

	result = remote_call(
		peer,
		"arab_streaming.api.passwords.fetch",
		**wire_identity(peer),
		event=capture_doc.remote_dispatch_event,
		version_name=version,
	)
	from frappe.utils.password import delete_login_failed_cache, passlibctx

	hashed = result.get("hash")
	if (
		not isinstance(hashed, str)
		or not 20 <= len(hashed) <= 512
		or passlibctx.identify(hashed) not in ("pbkdf2_sha256", "argon2")
	):
		frappe.throw("Invalid password version")
	frappe.db.sql(
		"INSERT INTO `__Auth` (doctype,name,fieldname,password,encrypted) VALUES ('User',%s,'password',%s,0) ON DUPLICATE KEY UPDATE password=VALUES(password),encrypted=0",
		(user, hashed),
	)
	frappe.db.set_value("User", user, "reset_password_key", "", update_modified=False)
	from frappe.sessions import clear_sessions

	clear_sessions(user=user, force=True)
	delete_login_failed_cache(user)
	frappe.clear_cache(user=user)
