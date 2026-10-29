import frappe
from frappe.model.document import Document

from arab_streaming.streaming.configuration import require_role
from arab_streaming.streaming.defaults import get_default_rows
from arab_streaming.streaming.guards import protect_fields, system_write_active
from arab_streaming.streaming.validation import normalize_site_url, validate_rules


class MasterInstance(Document):
	def get_invalid_links(self, is_submittable=False):
		branch = self.restaurant_branch
		invalid, cancelled = super().get_invalid_links(is_submittable=is_submittable)
		if self.slave_name and frappe.db.exists(
			"Streaming Peer State", {"instance_type": "Master Instance", "instance_name": self.name}
		):
			# Frappe also clears a missing Link's value while validating it.
			# Preserve only this authenticated remote identity until bootstrap.
			self.restaurant_branch = branch
			invalid = [item for item in invalid if item[0] != "restaurant_branch"]
		return invalid, cancelled

	def before_insert(self):
		require_role("Slave")
		self.connection_status = "Disconnected"
		self.syncing_status = "Not Started"
		self.enabled = 1
		for name in (
			"restaurant_branch",
			"slave_name",
			"receiving_doctypes_data",
			"exceptions",
			"last_requested_connection",
			"last_successful_connection",
			"last_connection_exception",
			"last_capture_exception",
			"last_dispatch_exception",
		):
			self.set(name, None)
		self.progress = self.total_syncing_document = 0
		for row in self.initial_sync_doctypes:
			row.synced = row.document_count = row.mandatory = 0
		if not self.initial_sync_doctypes:
			for row in get_default_rows("initial", "Slave"):
				self.append("initial_sync_doctypes", row)
		if not self.syncing_doctypes:
			for row in get_default_rows("syncing", "Slave"):
				self.append("syncing_doctypes", row)

	def validate(self):
		require_role("Slave")
		protect_fields(
			self,
			(
				"restaurant_branch",
				"connection_status",
				"syncing_status",
				"enabled",
				"total_syncing_document",
				"progress",
				"slave_name",
				"exceptions",
				"last_requested_connection",
				"last_successful_connection",
				"last_connection_exception",
				"last_capture_exception",
				"last_dispatch_exception",
				"receiving_doctypes_data",
			),
		)
		previous = self.get_doc_before_save()
		if previous and not system_write_active():
			if previous.slave_name and self.execution_user != previous.execution_user:
				frappe.throw(
					"A linked Execution User cannot change without an explicit pairing reauthorization"
				)
			old_rows = {r.name: r for r in previous.initial_sync_doctypes}
			for row in self.initial_sync_doctypes:
				old = old_rows.get(row.name)
				for key in ("synced", "document_count", "mandatory"):
					if frappe.utils.cint(row.get(key)) != frappe.utils.cint(old.get(key) if old else 0):
						frappe.throw("Initial-copy progress is system managed")
			if previous.syncing_status == "In Progress":
				frappe.throw("Master Instance configuration cannot change during initial copy")
			if previous.slave_name and any(
				self.get(k) != previous.get(k) for k in ("domain", "access_key", "secret", "site_domain")
			):
				frappe.throw("Linked credentials and domains require the connection replacement action")
			config_fields = (
				"doctype_name",
				"conditions",
				"sync_from",
				"ignore_links",
				"skip_if_exists",
				"skip_links_if_exists",
				"ignore_branch",
			)
			old_config = [
				tuple(row.get(key) for key in config_fields) for row in previous.initial_sync_doctypes
			]
			new_config = [tuple(row.get(key) for key in config_fields) for row in self.initial_sync_doctypes]
			if previous.syncing_status != "Complete" and old_config != new_config:
				self.syncing_status = "Not Started"
				self.total_syncing_document = self.progress = 0
				for row in self.initial_sync_doctypes:
					row.document_count = row.synced = 0
		self.domain = normalize_site_url(self.domain)
		self.site_domain = normalize_site_url(self.site_domain or frappe.utils.get_url())
		validate_rules(self)

	def on_trash(self):
		if self.connection_status == "Connected":
			frappe.throw("Disconnect the Master Instance before deleting it")
