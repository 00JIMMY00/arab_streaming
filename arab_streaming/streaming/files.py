"""Content-addressed file versions; no arbitrary URL downloads."""

import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import frappe

from arab_streaming.streaming.guards import system_write

MAX_FILE = 25 * 1024 * 1024
CHUNK = 128 * 1024


def references(document):
	meta = frappe.get_meta(document["doctype"])
	for field in meta.fields:
		value = document.get(field.fieldname)
		if field.fieldtype in ("Table", "Table MultiSelect"):
			for child in value or []:
				yield from references(child)
		elif field.fieldtype in ("Attach", "Attach Image") and value:
			yield document, field.fieldname, value


def version_path(checksum):
	if not re.fullmatch("[a-f0-9]{64}", checksum):
		frappe.throw("Invalid file digest")
	return Path(frappe.get_site_path("private", "files", ".arab_streaming_versions", checksum))


def freeze(document):
	result = []
	total = 0
	for _doc, _field, url in references(document):
		if not isinstance(url, str):
			frappe.throw("Invalid attachment URL")
		if url.startswith("https://"):
			parsed = urlsplit(url)
			if parsed.username or parsed.password:
				frappe.throw("Attachment URLs cannot contain credentials")
			continue
		if not url.startswith(("/files/", "/private/files/")):
			frappe.throw("Use a site attachment or HTTPS image URL")
		name = frappe.db.get_value("File", {"file_url": url}, "name")
		if not name:
			frappe.throw("Attachment is missing from File storage: " + frappe.utils.escape_html(url))
		file = frappe.get_doc("File", name)
		content = file.get_content()
		if isinstance(content, str):
			content = content.encode()
		total += len(content)
		if len(content) > MAX_FILE or total > 64 * 1024 * 1024:
			frappe.throw("File snapshot exceeds the configured transfer budget")
		checksum = hashlib.sha256(content).hexdigest()
		path = version_path(checksum)
		path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
		if not path.exists():
			with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".writing-", delete=False) as stream:
				temporary = Path(stream.name)
				stream.write(content)
				stream.flush()
				os.fsync(stream.fileno())
			try:
				os.link(temporary, path)
			except FileExistsError:
				pass
			finally:
				temporary.unlink(missing_ok=True)
		result.append(
			dict(
				file_url=url,
				file_name=file.file_name,
				is_private=int(file.is_private),
				size=len(content),
				sha256=checksum,
			)
		)
	return result


def manifest_for(peer, doctype, name, root_event=None, export=None):
	from arab_streaming.streaming.policy import authorize_document

	if export:
		from arab_streaming.streaming.connections import peer_state
		from arab_streaming.streaming.exports import authorized_export

		state = peer_state(peer.doctype, peer.name)
		authorized_export(state, export)
		row = frappe.db.get_value(
			"Streaming Snapshot Item",
			{"export": export, "reference_doctype": doctype, "reference_name": name},
			["data", "files"],
			as_dict=True,
		)
		if not row:
			frappe.throw("Attachment document is not in this export", frappe.PermissionError)
		document = frappe.parse_json(row.data)
		authorize_document(peer, document, dependency=True)
		return frappe.parse_json(row.files or "[]")
	if root_event:
		event = frappe.get_doc("Dispatch Event", root_event)
		if not any(r.instance_type == peer.doctype and r.instance_name == peer.name for r in event.instances):
			frappe.throw("Event is not addressed to this peer", frappe.PermissionError)
		document = frappe.parse_json(event.data)["document"]
		authorize_document(peer, document)
		if document["doctype"] == doctype and document["name"] == name:
			return frappe.parse_json(event.metadata or "{}").get("files") or []
		version = frappe.db.get_value(
			"Streaming Event Dependency",
			{"dispatch_event": event.name, "reference_doctype": doctype, "reference_name": name},
			"document_version",
		)
		if not version:
			frappe.throw("Attachment is not in this event manifest", frappe.PermissionError)
		row = frappe.db.get_value("Streaming Document Version", version, ["files", "data"], as_dict=True)
		authorize_document(peer, frappe.parse_json(row.data), dependency=True)
		return frappe.parse_json(row.files or "[]")
	frappe.throw("An event or immutable export is required for file access", frappe.PermissionError)


def import_files(peer, document, export=None):
	refs = [r for r in references(document) if r[2].startswith(("/files/", "/private/files/"))]
	if not refs:
		return
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import wire_identity

	context = dict(
		root_event=getattr(frappe.local, "streaming_current_event", None),
		export=export,
		doctype=document["doctype"],
		name=document["name"],
	)
	manifest = remote_call(peer, "arab_streaming.api.files.manifest", **wire_identity(peer), **context)[
		"files"
	]
	by_url = {r["file_url"]: r for r in manifest}
	imported = {}
	for owner, field, url in refs:
		item = by_url.get(url)
		if not item or not 0 <= int(item["size"]) <= MAX_FILE:
			frappe.throw("Attachment is not in the authenticated manifest")
		if url not in imported:
			checksum = item.get("sha256", "")
			if not re.fullmatch("[a-f0-9]{64}", checksum):
				frappe.throw("Invalid incoming file hash")
			folder = Path(frappe.get_site_path("private", "files", ".arab_streaming_incoming"))
			folder.mkdir(parents=True, exist_ok=True, mode=0o700)
			partial = folder / (checksum + ".part")
			# Independent peers/files cannot append to the same checkpoint concurrently.
			lease = frappe.cache.lock("streaming-file:" + frappe.local.site + ":" + checksum, timeout=300)
			with lease:
				fetch_file(peer, context, url, item, partial)
				content = partial.read_bytes()
			# Supply the verified bytes to File's normal upload lifecycle. Starting
			# from a hash-deduplicated URL can incorrectly demand access to an
			# unrelated existing private File before our upload is created.
			file = frappe.get_doc(
				dict(
					doctype="File",
					file_name=item["file_name"],
					content=content,
					is_private=item["is_private"],
				)
			)
			try:
				file.insert(ignore_permissions=True)
			except frappe.DuplicateEntryError:
				file = frappe.get_doc("File", file.duplicate_entry)
			imported[url] = file.file_url
		owner[field] = imported[url]


def fetch_file(peer, context, url, item, partial):
	from arab_streaming.streaming.client import remote_call
	from arab_streaming.streaming.connections import wire_identity

	if partial.exists() and partial.stat().st_size > item["size"]:
		partial.unlink()
	offset = partial.stat().st_size if partial.exists() else 0
	fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
	with os.fdopen(fd, "ab") as stream:
		while offset < item["size"]:
			data = remote_call(
				peer,
				"arab_streaming.api.files.chunk",
				**wire_identity(peer),
				**context,
				file_url=url,
				sha256=item["sha256"],
				offset=offset,
			)
			part = base64.b64decode(data["content"], validate=True)
			if not part or len(part) > CHUNK or offset + len(part) > item["size"]:
				frappe.throw("Invalid file transfer chunk")
			stream.write(part)
			stream.flush()
			os.fsync(stream.fileno())
			offset += len(part)
	if hashlib.sha256(partial.read_bytes()).hexdigest() != item["sha256"]:
		partial.unlink()
		frappe.throw("Transferred file content does not match its SHA-256 digest")


def attach(document):
	for _owner, _field, url in references(document):
		for name in frappe.get_all(
			"File", filters={"file_url": url, "attached_to_name": ["is", "not set"]}, pluck="name"
		):
			file = frappe.get_doc("File", name)
			file.attached_to_doctype = document["doctype"]
			file.attached_to_name = document["name"]
			file.save(ignore_permissions=True)


def changed(file, method=None):
	from arab_streaming.streaming.context import replay_source

	if replay_source() or not frappe.conf.get("arab_streaming_enabled"):
		return
	if (
		file.attached_to_doctype
		and file.attached_to_name
		and frappe.db.exists(file.attached_to_doctype, file.attached_to_name)
	):
		from arab_streaming.streaming.adapters import SAFE_TYPES

		if file.attached_to_doctype in SAFE_TYPES:
			parent = frappe.get_doc(file.attached_to_doctype, file.attached_to_name)
			if parent.docstatus == 0 and parent.has_permission("write"):
				parent.save()
