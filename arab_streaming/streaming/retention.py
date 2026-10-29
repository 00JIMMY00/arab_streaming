"""History cleanup keeps durable receipts, record checkpoints and active holds."""

import json
import time

import frappe

from arab_streaming.arab_streaming.doctype.streaming_settings.streaming_settings import (
	effective_retention_days,
)
from arab_streaming.streaming.guards import system_write


def cleanup():
	if not frappe.conf.get("arab_streaming_enabled"):
		return
	# A failed/stopped initial run retains its evidence until Continue or explicit Restart.
	if frappe.db.exists("Streaming Initial Sync Run", {"retention_hold": 1}):
		return
	days = effective_retention_days(
		frappe.db.get_single_value("Streaming Settings", "clear_events_after_days")
	)
	before = frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-days)
	with system_write():
		for doctype, statuses in (
			("Dispatch Event", ["Applied", "Skipped", "Completed with Skips"]),
			("Capture Event", ["Success", "Skipped"]),
		):
			for name in frappe.get_all(
				doctype,
				filters={"status": ["in", statuses], "creation": ["<", before]},
				pluck="name",
				limit_page_length=100,
			):
				from arab_streaming.streaming.stock import safe_to_remove

				if not safe_to_remove(frappe.get_doc(doctype, name)):
					continue
				if doctype == "Dispatch Event":
					run = frappe.db.get_value("Dispatch Event", name, "copy_run")
					if run and frappe.db.get_value("Streaming Copy Run", run, "status") not in ("Complete", "Stopped"):
						continue
					# Export boundaries rely on addressed history until their explicit release.
					if frappe.db.exists("Streaming Export", {"status": "In Progress"}):
						continue
					frappe.db.delete("Streaming Event Dependency", {"dispatch_event": name})
				if doctype == "Capture Event":
					from arab_streaming.streaming.feedback import remember_result

					remember_result(frappe.get_doc(doctype, name))
				frappe.delete_doc(doctype, name, ignore_permissions=True)
			frappe.db.commit()
		for export in frappe.get_all(
			"Streaming Export",
			filters={"status": ["in", ["Complete", "Abandoned"]], "creation": ["<", before]},
			pluck="name",
			limit_page_length=50,
		):
			frappe.db.delete("Streaming Snapshot Item", {"export": export})
			# Local run retains remote_export as an opaque audit reference, not a local Link.
			frappe.delete_doc("Streaming Export", export, ignore_permissions=True)
		unreferenced = frappe.db.sql(
			"""SELECT version.name FROM `tabStreaming Document Version` version
            LEFT JOIN `tabStreaming Event Dependency` dependency ON dependency.document_version=version.name
            WHERE dependency.name IS NULL AND version.creation<%s LIMIT 100""",
			(before,),
			as_dict=True,
		)
		for row in unreferenced:
			frappe.delete_doc("Streaming Document Version", row.name, ignore_permissions=True)
		frappe.db.commit()
	# Include all retained manifests, not merely current File URLs. An age grace
	# prevents racing a source transaction that has written bytes but not committed.
	hashes = set()
	for row in frappe.get_all("Streaming Document Version", fields=["files"]):
		hashes.update(x["sha256"] for x in json.loads(row.files or "[]"))
	for row in frappe.get_all("Streaming Snapshot Item", fields=["files"]):
		hashes.update(x["sha256"] for x in json.loads(row.files or "[]"))
	for row in frappe.get_all("Dispatch Event", fields=["metadata"]):
		hashes.update(x["sha256"] for x in (frappe.parse_json(row.metadata or "{}").get("files") or []))
	from pathlib import Path

	folder = Path(frappe.get_site_path("private", "files", ".arab_streaming_versions"))
	if folder.exists():
		for path in folder.iterdir():
			if (
				path.is_file()
				and path.name not in hashes
				and time.time() - path.stat().st_mtime > max(days * 86400, 86400)
			):
				path.unlink()
