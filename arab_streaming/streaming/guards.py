from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe.model.document import Document

_system_write = ContextVar("arab_streaming_system_write", default=False)


@contextmanager
def system_write():
	"""Internal transaction services only. Never read this authority from posted flags."""
	token = _system_write.set(True)
	try:
		yield
	finally:
		_system_write.reset(token)


def system_write_active():
	return _system_write.get()


def require_system_write():
	if not system_write_active():
		frappe.throw(
			"Streaming operational records can only be changed by streaming services", frappe.PermissionError
		)


def require_administrator():
	if frappe.session.user != "Administrator":
		frappe.throw("Only Administrator can perform this action", frappe.PermissionError)


def protect_fields(doc, fields):
	previous = doc.get_doc_before_save()
	if previous and not system_write_active():
		for name in fields:
			if doc.get(name) != previous.get(name):
				frappe.throw(f"{doc.meta.get_label(name)} is system managed", frappe.PermissionError)


def require_immutable_fields(doc, fields):
	previous = doc.get_doc_before_save()
	if previous:
		for name in fields:
			current_value, previous_value = doc.get(name), previous.get(name)
			fieldtype = doc.meta.get_field(name).fieldtype
			if fieldtype in ("Int", "Long Int", "Check"):
				current_value, previous_value = (
					frappe.utils.cint(current_value),
					frappe.utils.cint(previous_value),
				)
			elif fieldtype == "Datetime":
				current_value = frappe.utils.get_datetime(current_value) if current_value else None
				previous_value = frappe.utils.get_datetime(previous_value) if previous_value else None
			elif fieldtype == "JSON":
				from arab_streaming.streaming.validation import validate_json_object

				current_value = validate_json_object(current_value)
				previous_value = validate_json_object(previous_value)
			if current_value != previous_value:
				frappe.throw(f"{doc.meta.get_label(name)} cannot change after creation")


class ManagedDocument(Document):
	def before_insert(self):
		require_system_write()

	def validate(self):
		require_system_write()

	def on_trash(self):
		require_system_write()
