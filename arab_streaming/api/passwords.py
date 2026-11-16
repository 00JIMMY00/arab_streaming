import frappe

from arab_streaming.api.events import addressed


@frappe.whitelist(methods=["POST"])
def fetch(
	protocol,
	version,
	pairing_id,
	source_installation_id,
	pairing_generation,
	event,
	version_name,
):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.user_passwords import fetch as fetch_version

	return fetch_version(peer, event, version_name)


@frappe.whitelist(methods=["POST"])
def ready(protocol, version, pairing_id, source_installation_id, pairing_generation):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.policy import config_for

	config = config_for(peer, "User", incoming=True)
	from arab_streaming.streaming.user_passwords import administrator_enabled

	return {
		"supported": True,
		"enabled": bool(peer.doctype == "Master Instance" and config and config.include_passwords),
		"administrator_enabled": administrator_enabled(),
	}
