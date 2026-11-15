import unittest
from unittest.mock import Mock, patch

import frappe

from arab_streaming.streaming.exports import selection, write_snapshot_items
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.initial_sync import include_existing_names, operator_configuration


class TestBootstrapSelection(unittest.TestCase):
	def test_internal_snapshot_rows_are_batched_with_their_exact_payload(self):
		item = dict(
			export="export-1",
			row_key="row-1",
			position=0,
			reference_doctype="Item",
			reference_name="item-1",
			data='{"name":"item-1"}',
			files="[]",
			payload_digest="digest",
		)
		with (
			patch.object(frappe, "db", Mock()) as db,
			patch.object(frappe, "session", frappe._dict(user="wire@example.invalid")),
			patch("frappe.utils.now_datetime", return_value="2026-11-15 00:00:00"),
			system_write(),
		):
			write_snapshot_items([item])
			doctype, fields, values = db.bulk_insert.call_args.args
			self.assertEqual(doctype, "Streaming Snapshot Item")
			stored = dict(zip(fields, values[0], strict=False))
			self.assertEqual(stored["data"], item["data"])
			self.assertEqual(stored["payload_digest"], item["payload_digest"])
			self.assertEqual(stored["position"], 0)
			self.assertEqual(stored["owner"], "wire@example.invalid")

	def test_bulk_artifact_writer_requires_internal_system_context(self):
		with (
			patch(
				"arab_streaming.streaming.exports.require_system_write", side_effect=frappe.PermissionError
			),
			patch.object(frappe, "db", Mock()) as db,
		):
			with self.assertRaises(frappe.PermissionError):
				write_snapshot_items([])
			db.bulk_insert.assert_not_called()

	def test_existing_ids_only_narrow_preserve_existing_rows(self):
		row = dict(doctype_name="Country", skip_if_exists=1, existing_names=["Egypt"])
		with (
			patch("arab_streaming.streaming.exports.require_supported"),
			patch("arab_streaming.streaming.exports.condition_fields", return_value={"name"}),
		):
			self.assertEqual(selection(row), [["name", "not in", ["Egypt"]]])

	def test_invalid_exclusions_cannot_change_update_policy(self):
		def reject(message):
			raise frappe.ValidationError(message)

		with (
			patch("arab_streaming.streaming.exports.require_supported"),
			patch("arab_streaming.streaming.exports.condition_fields", return_value={"name"}),
			patch("frappe.throw", side_effect=reject),
		):
			for value in ("Egypt", [None], [""], list(range(10001))):
				with self.assertRaises(frappe.ValidationError):
					selection(dict(doctype_name="Country", skip_if_exists=1, existing_names=value))
			with self.assertRaises(frappe.ValidationError):
				selection(dict(doctype_name="Company", skip_if_exists=0, existing_names=["Existing"]))

	def test_baseline_is_frozen_and_company_scaffolds_are_not_excluded(self):
		rows = [
			dict(doctype_name="Country", skip_if_exists=1),
			dict(doctype_name="Company", skip_if_exists=0),
		]
		with patch("frappe.get_list", return_value=["Egypt"]) as get_list:
			frozen = include_existing_names(rows)
			self.assertEqual(frozen[0]["existing_names"], ["Egypt"])
			self.assertNotIn("existing_names", frozen[1])
			self.assertEqual(operator_configuration(frozen), rows)
			get_list.reset_mock()
			self.assertEqual(include_existing_names(frozen), frozen)
			get_list.assert_not_called()

	def test_large_receiver_tables_keep_the_original_copy_path(self):
		with patch("frappe.get_list", return_value=[str(i) for i in range(10001)]):
			self.assertEqual(
				include_existing_names([dict(doctype_name="Customer", skip_if_exists=1)])[0][
					"existing_names"
				],
				[],
			)
