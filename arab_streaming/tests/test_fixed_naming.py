import unittest
from unittest.mock import Mock, patch

import frappe

from arab_streaming.streaming import naming


class TestFixedSeries(unittest.TestCase):
	def test_native_fixed_transaction_counter_is_partitioned(self):
		meta = frappe._dict(is_submittable=1, autoname="POS-OPE-.YYYY.-.#####")
		meta.has_field = lambda _: False
		doc = frappe._dict(doctype="POS Opening Entry", meta=meta)
		with (
			patch.object(frappe, "conf", frappe._dict(arab_streaming_enabled=1)),
			patch.object(frappe, "db", Mock()) as db,
			patch.object(
				naming,
				"stored_rules",
				return_value=[
					frappe._dict(
						enabled=1,
						doctype_name="POS Opening Entry",
						direction="Slave to Master",
						scope="Branch",
					)
				],
			),
			patch.object(naming, "direction_allowed", return_value=True),
			patch.object(naming, "check_source"),
			patch.object(naming, "document_branches", return_value={"A"}),
			patch("frappe.model.naming.make_autoname", return_value="partitioned") as make,
		):
			db.get_value.return_value = "aabbccdd-0000-0000-0000-000000000000"
			naming.fixed_series_autoname(doc)
			self.assertEqual(doc.name, "partitioned")
			self.assertTrue(make.call_args.args[0].startswith("BR-559AEA-WR-AABBCC-POS-OPE-"))

	def test_user_contact_job_ignores_client_flags_without_server_record(self):
		from arab_streaming.streaming.user_contacts import before_job

		with (
			patch.object(frappe, "conf", frappe._dict(arab_streaming_site_role="Slave")),
			patch.object(frappe, "db", Mock()) as db,
			patch("arab_streaming.streaming.context.replay") as replay,
		):
			db.get_value.return_value = None
			before_job(
				"frappe.core.doctype.user.user.create_contact",
				{"user": {"name": "cashier@example.invalid", "flags": {"streaming_source": "forged"}}},
			)
			replay.assert_not_called()


class TestNativeUserContactNormalization(unittest.TestCase):
	def test_only_generated_phone_email_ids_can_be_normalized(self):
		from copy import deepcopy

		from arab_streaming.streaming.mapping import same_native_user_contact

		source = {
			"doctype": "Contact",
			"name": "c",
			"user": "u@example.invalid",
			"phone_nos": [{"name": "source-row", "phone": "123"}],
			"links": [],
		}
		target = deepcopy(source)
		target["phone_nos"][0]["name"] = "local-row"
		self.assertTrue(same_native_user_contact(source, target))
		target["phone_nos"][0]["phone"] = "456"
		self.assertFalse(same_native_user_contact(source, target))
		target = deepcopy(source)
		target["user"] = "other@example.invalid"
		self.assertFalse(same_native_user_contact(source, target))
