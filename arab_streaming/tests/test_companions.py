import json
import unittest
from unittest.mock import Mock, patch

import frappe

from arab_streaming.streaming import companions


class TestCompanionCancellation(unittest.TestCase):
	def fixtures(self):
		target = Mock(doctype="Stock Entry")
		target.name = "STE"
		state = frappe._dict(
			instance_type="Slave Instance",
			instance_name="peer",
			pairing_id="pair",
			pairing_generation=1,
			execution_user=frappe.session.user,
		)
		peer = frappe._dict(doctype="Slave Instance", name="peer")
		capture = frappe._dict(
			data=json.dumps(
				dict(document=dict(doctype="Stock Entry", name="STE", docstatus=2), base_digest="original")
			),
			doctype_config="{}",
		)
		return target, state, peer, capture

	def test_native_cancel_and_checkpoint_use_exact_addressed_source(self):
		target, state, peer, capture = self.fixtures()
		with (
			patch.object(companions, "replay_source", return_value="pair"),
			patch("frappe.db.get_value", side_effect=["state", None, "cancel-event"]) as lookup,
			patch("frappe.get_doc", side_effect=[state, peer, capture]),
			patch("arab_streaming.streaming.policy.authorize_document") as authorize,
			patch("arab_streaming.streaming.mapping.check_base") as check,
			patch("arab_streaming.streaming.mapping.remember") as remember,
		):
			companions.cancel(target)
			check.assert_called_once_with(peer, json.loads(capture.data)["document"], target, "original")
			authorize.assert_called_once_with(peer, json.loads(capture.data)["document"], incoming=True)
			remember.assert_called_once_with(peer, json.loads(capture.data)["document"], target)
			filters = lookup.call_args_list[-1].args[1]
			self.assertEqual(
				(filters["pairing_id"], filters["pairing_generation"], filters["ignore"]), ("pair", 1, 0)
			)
		target.cancel.assert_called_once_with()
		target.check_permission.assert_any_call("cancel")

	def test_no_replay_cannot_cancel(self):
		target, *_ = self.fixtures()
		with patch.object(companions, "replay_source", return_value=None):
			with self.assertRaises(frappe.PermissionError):
				companions.cancel(target)
		target.cancel.assert_not_called()

	def test_missing_addressed_cancel_never_reverses_stock(self):
		target, state, peer, _ = self.fixtures()
		with (
			patch.object(companions, "replay_source", return_value="pair"),
			patch("frappe.db.get_value", side_effect=["state", None, None]),
			patch("frappe.get_doc", side_effect=[state, peer]),
		):
			with self.assertRaises(frappe.ValidationError):
				companions.cancel(target)
		target.cancel.assert_not_called()

	def test_local_concurrent_change_prevents_cancellation(self):
		target, state, peer, capture = self.fixtures()
		with (
			patch.object(companions, "replay_source", return_value="pair"),
			patch("frappe.db.get_value", side_effect=["state", None, "event"]),
			patch("frappe.get_doc", side_effect=[state, peer, capture]),
			patch("arab_streaming.streaming.policy.authorize_document"),
			patch(
				"arab_streaming.streaming.mapping.check_base", side_effect=frappe.ValidationError("conflict")
			) as check,
		):
			with self.assertRaises(frappe.ValidationError):
				companions.cancel(target)
			check.assert_called_once()
		target.cancel.assert_not_called()
