import frappe

from arab_streaming.api.events import addressed


@frappe.whitelist(methods=["POST"])
def snapshot(protocol, version, pairing_id, source_installation_id, pairing_generation):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.stock import produce

	return produce(peer, state)


@frappe.whitelist(methods=["POST"])
def acknowledge(
	protocol, version, pairing_id, source_installation_id, pairing_generation, snapshot, checksum
):
	peer, state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	from arab_streaming.streaming.stock import acknowledge as mark

	return mark(peer, state, snapshot, checksum)
