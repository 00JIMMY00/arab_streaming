import base64

import frappe

from arab_streaming.api.events import addressed
from arab_streaming.streaming.files import CHUNK, manifest_for, version_path


@frappe.whitelist(methods=["POST"])
def manifest(
	protocol,
	version,
	pairing_id,
	source_installation_id,
	pairing_generation,
	doctype,
	name,
	root_event=None,
	export=None,
):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	return {"files": manifest_for(peer, doctype, name, root_event, export)}


@frappe.whitelist(methods=["POST"])
def chunk(
	protocol,
	version,
	pairing_id,
	source_installation_id,
	pairing_generation,
	doctype,
	name,
	file_url,
	sha256,
	offset=0,
	root_event=None,
	export=None,
):
	peer, _state = addressed(protocol, version, pairing_id, source_installation_id, pairing_generation)
	rows = manifest_for(peer, doctype, name, root_event, export)
	item = next((r for r in rows if r["file_url"] == file_url and r["sha256"] == sha256), None)
	if not item:
		frappe.throw("File is not authorized by this manifest", frappe.PermissionError)
	offset = int(offset)
	if not 0 <= offset <= int(item["size"]):
		frappe.throw("Invalid file offset")
	path = version_path(sha256)
	if not path.is_file() or path.stat().st_size != item["size"]:
		frappe.throw("Immutable file content is missing; reconcile the source file version")
	with path.open("rb") as stream:
		stream.seek(offset)
		content = stream.read(CHUNK)
	return {"content": base64.b64encode(content).decode(), "offset": offset, "size": item["size"]}
