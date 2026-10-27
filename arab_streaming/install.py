import uuid

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from arab_streaming.streaming.guards import system_write


def after_install():
	ensure_configuration()


def after_migrate():
	ensure_configuration()


def ensure_configuration():
	"""Idempotent site schema setup; never configure a role or select business data."""
	if not frappe.db.exists("DocType", "Branch"):
		frappe.throw("Arab-ERP Streaming requires ERPNext v15 and its Branch DocType")
	if not frappe.db.exists("Custom Field", {"dt": "Branch", "fieldname": "custom_streaming_enabled"}):
		create_custom_fields(
			{
				"Branch": [
					dict(
						fieldname="custom_streaming_enabled",
						label="Enable Arab-ERP Streaming",
						fieldtype="Check",
						default="0",
						insert_after="branch",
						description="Explicitly allow this branch to participate in Arab-ERP Streaming.",
					)
				]
			}
		)
	with system_write():
		if not frappe.db.exists("Streaming Site State", "local"):
			frappe.get_doc(
				dict(
					doctype="Streaming Site State",
					name="local",
					installation_id=str(uuid.uuid4()),
					event_sequence=0,
				)
			).insert(ignore_permissions=True)
	# Defaults are written once; migration must preserve zero/off operator choices.
	if not frappe.db.sql("SELECT 1 FROM tabSingles WHERE doctype=%s LIMIT 1", ("Streaming Settings",)):
		frappe.get_doc("Streaming Settings").save(ignore_permissions=True)
	ensure_indexes()


def ensure_indexes():
	unique = {
		"Capture Event": [
			"source_installation_id",
			"pairing_id",
			"pairing_generation",
			"remote_dispatch_event",
		],
		"Dispatch Event Instance": ["parent", "instance_type", "instance_name"],
		"Streaming Event Receipt": [
			"source_installation_id",
			"pairing_id",
			"pairing_generation",
			"remote_dispatch_event",
		],
		"Streaming Peer State": ["instance_type", "instance_name"],
		"Streaming Initial Sync Run": ["master_instance", "generation"],
		"Streaming Sync Checkpoint": ["parent", "row_key"],
	}
	for doctype, columns in unique.items():
		# v15 add_unique inspects information_schema without TABLE_SCHEMA.
		# Scope the check to this site's actual table on a shared MariaDB server.
		if not frappe.db.has_index(f"tab{doctype}", "streaming_identity"):
			frappe.db.commit()
			quoted = ", ".join(f"`{column}`" for column in columns)
			frappe.db.sql_ddl(f"ALTER TABLE `tab{doctype}` ADD UNIQUE INDEX `streaming_identity` ({quoted})")
	indexes = {
		"Capture Event": [
			(["instance_type", "instance_name", "status", "event_sequence"], "streaming_pending"),
			(["reference_doctype", "reference_name"], "streaming_reference"),
			(["status", "creation"], "streaming_cleanup"),
		],
		"Dispatch Event Instance": [
			(["instance_type", "instance_name", "synced", "`ignore`", "next_attempt_at"], "streaming_pending")
		],
		"Dispatch Event": [
			(["event_sequence"], "streaming_sequence"),
			(["status", "creation"], "streaming_cleanup"),
			(["reference_doctype", "reference_name"], "streaming_reference"),
		],
	}
	for doctype, definitions in indexes.items():
		for columns, name in definitions:
			frappe.db.add_index(doctype, columns, index_name=name)
