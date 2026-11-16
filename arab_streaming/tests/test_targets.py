import unittest
from unittest.mock import Mock, patch

import frappe

from arab_streaming.auth import WIRE_METHODS
from arab_streaming.streaming import targets


class TestConnectionTargets(unittest.TestCase):
	def test_default_catalog_preserves_branch_pairing(self):
		with (
			patch.object(targets, "provider", return_value=None),
			patch("frappe.get_all", return_value=["used"]),
			patch("frappe.get_list", return_value=[frappe._dict(name="used"), frappe._dict(name="free")]),
		):
			self.assertEqual(
				targets.choices(),
				{"doctype": "Branch", "label": "Branch", "targets": [{"name": "free", "label": "free"}]},
			)
			self.assertEqual(targets.resolve("free"), {"branch": "free"})
			with self.assertRaises(frappe.PermissionError):
				targets.resolve("used")

	def test_installed_provider_owns_target_resolution(self):
		with patch.object(targets, "provider", return_value={"branch": "internal"}) as provider:
			self.assertEqual(targets.resolve("my-store"), {"branch": "internal"})
			provider.assert_called_once_with("resolve", target="my-store")

	def test_invalid_target_never_reaches_a_provider(self):
		with patch.object(targets, "provider") as provider:
			for value in (None, "", {}, "x" * 141):
				with self.assertRaises(frappe.ValidationError):
					targets.resolve(value)
			provider.assert_not_called()

	def test_two_target_providers_fail_explicitly(self):
		with patch("frappe.get_hooks", return_value=["a", "b"]), self.assertRaises(frappe.ValidationError):
			targets.descriptor()

	def test_link_resolves_store_on_authenticated_remote_before_native_pairing(self):
		doc = frappe._dict(doctype="Master Instance", name="peer", slave_name=None)
		with (
			patch("arab_streaming.streaming.connections.master_connection", return_value=doc),
			patch(
				"arab_streaming.streaming.client.remote_call", return_value={"branch": "internal"}
			) as remote,
			patch(
				"arab_streaming.streaming.connections.link_connection", return_value={"status": "Connected"}
			) as link,
		):
			targets.link_target("peer", "Store A")
			remote.assert_called_once_with(
				doc, "arab_streaming.api.connection.resolve_target", target="Store A"
			)
			link.assert_called_once_with("peer", "internal")

	def test_existing_pair_cannot_move_to_another_store(self):
		doc = frappe._dict(slave_name="slave", restaurant_branch="internal")
		with (
			patch("arab_streaming.streaming.connections.master_connection", return_value=doc),
			patch.object(targets, "describe", return_value={"name": "Store A"}),
			patch("arab_streaming.streaming.connections.link_connection") as link,
		):
			with self.assertRaises(frappe.ValidationError):
				targets.link_target("peer", "Store B")
			link.assert_not_called()

	def test_target_wire_methods_are_allowlisted_and_authentication_is_required(self):
		from arab_streaming.api import connection

		for method in ("targets", "resolve_target"):
			self.assertIn("arab_streaming.api.connection." + method, WIRE_METHODS)
		with (
			patch.object(connection, "authorize", side_effect=frappe.AuthenticationError),
			patch.object(targets, "choices") as choices,
		):
			with self.assertRaises(frappe.AuthenticationError):
				connection.targets("p", "v")
			choices.assert_not_called()
