from contextlib import contextmanager
from contextvars import ContextVar

import frappe

_replay = ContextVar("streaming_replay", default=None)


def replay_source():
	return _replay.get()


@contextmanager
def replay(source, user):
	if user in ("Administrator", "Guest") or not frappe.db.get_value("User", user, "enabled"):
		frappe.throw("Streaming execution requires an approved enabled integration User")
	previous_user = frappe.session.user
	token = _replay.set(source)
	try:
		frappe.set_user(user)
		yield
	finally:
		frappe.set_user(previous_user)
		_replay.reset(token)
