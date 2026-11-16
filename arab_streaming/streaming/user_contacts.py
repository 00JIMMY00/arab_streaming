"""Keep native queued User contact normalization inside its trusted replay origin."""

import frappe


def before_job(method, kwargs, transaction_type=None):
	if (
		method != "frappe.core.doctype.user.user.create_contact"
		or frappe.conf.get("arab_streaming_site_role") != "Slave"
	):
		return
	user = kwargs.get("user")
	name = user.get("name") if isinstance(user, dict) else getattr(user, "name", None)
	if not name:
		return
	tracked = frappe.db.get_value(
		"Streaming Record State", {"reference_doctype": "User", "local_name": name}, "peer_state"
	)
	if not tracked:
		return
	state = frappe.get_doc("Streaming Peer State", tracked)
	if state.instance_type != "Master Instance":
		return
	peer = frappe.get_doc("Master Instance", state.instance_name)
	if peer.connection_status != "Connected":
		return
	from arab_streaming.streaming.policy import config_for

	rule = config_for(peer, "User", incoming=True)
	if not rule or not rule.lock_documents:
		return
	from arab_streaming.streaming.context import replay

	manager = replay(state.pairing_id, state.execution_user)
	manager.__enter__()
	frappe.local.streaming_user_contact_job = (manager, name, tracked)


def after_job(method, kwargs, transaction_type=None):
	context = getattr(frappe.local, "streaming_user_contact_job", None)
	if not context:
		return
	manager, user, peer_state = context
	try:
		from arab_streaming.protocol.codec import digest, snapshot
		from arab_streaming.streaming.guards import system_write

		for contact in frappe.get_all("Contact", filters={"user": user}, pluck="name"):
			tracked = frappe.db.get_value(
				"Streaming Record State",
				{"reference_doctype": "Contact", "local_name": contact, "peer_state": peer_state},
				"name",
			)
			if tracked:
				with system_write():
					frappe.db.set_value(
						"Streaming Record State",
						tracked,
						"local_digest",
						digest(snapshot(frappe.get_doc("Contact", contact))),
						update_modified=False,
					)
		# Frappe commits the business job before calling after_job hooks.
		frappe.db.commit()
	finally:
		manager.__exit__(None, None, None)
		frappe.local.streaming_user_contact_job = None
