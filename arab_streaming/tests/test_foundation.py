import itertools
import json
import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.arab_streaming.doctype.streaming_settings.streaming_settings import (
	effective_retention_days,
)
from arab_streaming.install import ensure_configuration
from arab_streaming.streaming.branches import get_enabled_branches
from arab_streaming.streaming.configuration import get_site_role
from arab_streaming.streaming.defaults import get_default_rows
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.state import next_sequence
from arab_streaming.streaming.status import dispatch_status
from arab_streaming.streaming.validation import normalize_site_url, validate_json_object


class TestDispatchStatus(unittest.TestCase):
	def test_all_destination_combinations(self):
		states = ({"synced": 0, "ignore": 0}, {"synced": 1, "ignore": 0}, {"synced": 0, "ignore": 1})
		self.assertEqual(dispatch_status([]), "Pending")
		for size in range(1, 5):
			for rows in itertools.product(states, repeat=size):
				with self.subTest(rows=rows):
					synced = sum(r["synced"] for r in rows)
					ignored = sum(r["ignore"] for r in rows)
					expected = (
						"Synced"
						if synced == size
						else "Ignored"
						if ignored == size
						else "Partially Synced"
						if synced
						else "Partially Ignored"
						if ignored
						else "Pending"
					)
					self.assertEqual(dispatch_status(rows), expected)
		with self.assertRaises(ValueError):
			dispatch_status([{"synced": 1, "ignore": 1}])


class TestFoundation(FrappeTestCase):
	def setUp(self):
		self.previous_role = frappe.conf.get("arab_streaming_site_role")
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		frappe.db.savepoint("foundation_test")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="foundation_test")
		frappe.conf.arab_streaming_site_role = self.previous_role
		frappe.set_user(self.previous_user)

	def make_user(self, manager=False):
		email = f"streaming-{frappe.generate_hash(length=10)}@example.invalid"
		return frappe.get_doc(
			dict(
				doctype="User",
				email=email,
				first_name="Streaming Test",
				enabled=1,
				send_welcome_email=0,
				roles=[{"role": "System Manager"}] if manager else [],
			)
		).insert()

	def make_master(self):
		frappe.conf.arab_streaming_site_role = "Slave"
		return frappe.get_doc(
			dict(
				doctype="Master Instance",
				domain="MASTER.example.invalid/",
				access_key="fake-access-key",
				secret="fake-secret",
				site_domain="https://branch.example.invalid",
			)
		)

	def test_unconfigured_role_is_inert_and_rejects_connections(self):
		frappe.conf.arab_streaming_site_role = None
		self.assertIsNone(get_site_role())
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				dict(
					doctype="Master Instance",
					domain="master.example.invalid",
					access_key="fake",
					secret="fake",
				)
			).insert()
		frappe.conf.arab_streaming_site_role = "invalid"
		with self.assertRaises(frappe.ValidationError):
			get_site_role()

	def test_master_initial_state_and_protected_fields(self):
		doc = self.make_master()
		doc.connection_status = "Connected"
		doc.syncing_status = "Complete"
		doc.slave_name = "forged-counterpart"
		doc.insert()
		self.assertEqual(doc.connection_status, "Disconnected")
		self.assertEqual(doc.syncing_status, "Not Started")
		self.assertFalse(doc.slave_name)
		self.assertEqual(doc.domain, "https://master.example.invalid")
		doc.connection_status = "Connected"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		doc.reload()
		with system_write():
			doc.connection_status = "Connected"
			doc.save()
		with self.assertRaises(frappe.ValidationError):
			doc.delete()

	def test_wrong_site_rejected_even_for_trusted_service(self):
		frappe.conf.arab_streaming_site_role = "Master"
		doc = self.make_master()
		frappe.conf.arab_streaming_site_role = "Master"
		with system_write(), self.assertRaises(frappe.ValidationError):
			doc.insert()
		frappe.conf.arab_streaming_site_role = "Slave"
		with system_write(), self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				dict(doctype="Slave Instance", restaurant_branch="missing", connection_status="Disconnected")
			).insert()

	def test_system_managed_write_cannot_be_forged_with_document_flags(self):
		doc = frappe.get_doc("Streaming Site State", "local")
		original = doc.installation_id
		doc.installation_id = "forged-installation"
		doc.flags.system_write = True
		doc.flags.ignore_permissions = True
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, "installation_id"), original)

	def test_settings_boundaries_and_zero_retention(self):
		doc = frappe.get_doc("Streaming Settings")
		doc.strict_stock_snapshot_validation = 1
		for age in (None, 0, 599):
			doc = frappe.get_doc("Streaming Settings")
			doc.strict_stock_snapshot_validation = 1
			doc.stock_snapshot_max_age = age
			with self.assertRaises(frappe.ValidationError):
				doc.save()
		doc = frappe.get_doc("Streaming Settings")
		doc.stock_snapshot_max_age = 600
		doc.clear_events_after_days = 0
		doc.save()
		self.assertEqual(effective_retention_days(doc.clear_events_after_days), 60)
		for value in (None, "", 0, "0"):
			self.assertEqual(effective_retention_days(value), 60)
		for value in (-1, "1.5"):
			with self.assertRaises((frappe.ValidationError, ValueError)):
				effective_retention_days(value)
		doc.strict_stock_snapshot_validation = 0
		doc.stock_snapshot_max_age = 0
		doc.save()

	def test_reloaded_singleton_duration_can_be_saved(self):
		doc = frappe.get_doc("Streaming Settings")
		doc.strict_stock_snapshot_validation = 1
		doc.stock_snapshot_max_age = 900
		doc.save()
		doc = frappe.get_doc("Streaming Settings")
		doc.clear_events_after_days = 14
		doc.save()
		self.assertEqual(frappe.utils.flt(doc.stock_snapshot_max_age), 900)

	def test_sequence_is_64_bit_exact_and_transactional(self):
		before = 2**53 + 117
		frappe.db.set_value("Streaming Site State", "local", "event_sequence", before)
		frappe.db.savepoint("sequence_allocation")
		with system_write():
			self.assertEqual(next_sequence(), before + 1)
			self.assertEqual(next_sequence(), before + 2)
		frappe.db.rollback(save_point="sequence_allocation")
		self.assertEqual(frappe.db.get_value("Streaming Site State", "local", "event_sequence"), before)
		with self.assertRaises(frappe.PermissionError):
			next_sequence()

	def test_sequence_allocation_locks_out_other_transactions(self):
		import pymysql

		with system_write():
			next_sequence()
		connection = pymysql.connect(**frappe.db.get_connection_settings())
		try:
			with connection.cursor() as cursor, self.assertRaises(pymysql.OperationalError) as error:
				cursor.execute(
					"SELECT event_sequence FROM `tabStreaming Site State` WHERE name='local' FOR UPDATE NOWAIT"
				)
			self.assertEqual(error.exception.args[0], 1205)
		finally:
			connection.rollback()
			connection.close()

	def test_unique_replay_identity_enforced_by_database(self):
		values = dict(
			doctype="Streaming Event Receipt",
			source_installation_id="test-source",
			pairing_id="test-pair",
			pairing_generation=1,
			remote_dispatch_event="event-1",
			payload_digest="a" * 64,
		)
		with system_write():
			frappe.get_doc(values).insert(ignore_permissions=True)
			with self.assertRaises(frappe.UniqueValidationError):
				frappe.get_doc(values).insert(ignore_permissions=True)
			frappe.get_doc({**values, "pairing_generation": 2}).insert(ignore_permissions=True)

	def test_metadata_requires_object_and_url_refuses_unsafe_forms(self):
		self.assertEqual(validate_json_object('{"description":"عربي"}'), {"description": "عربي"})
		for invalid in ("[]", "null", "1", "true", "{broken"):
			with self.assertRaises(frappe.ValidationError):
				validate_json_object(invalid)
		self.assertEqual(normalize_site_url("EXAMPLE.invalid:443/"), "https://example.invalid")
		for invalid in (
			"http://example.invalid",
			"https://user:secret@example.invalid",
			"https://example.invalid/path",
			"https://example.invalid/?token=secret",
			"https://example.invalid/#fragment",
			"https://example.invalid:bad",
			"https://example.invalid\\evil",
		):
			with self.assertRaises(frappe.ValidationError):
				normalize_site_url(invalid)

	def test_enabled_branch_provider(self):
		frappe.conf.arab_streaming_site_role = "Master"
		names = []
		for enabled in (0, 1):
			doc = frappe.get_doc(
				dict(
					doctype="Branch",
					branch=f"Streaming test {frappe.generate_hash(length=8)}",
					custom_streaming_enabled=enabled,
				)
			).insert()
			names.append(doc.name)
		selected = {row.name for row in get_enabled_branches()}
		self.assertNotIn(names[0], selected)
		self.assertIn(names[1], selected)

	def test_key_generation_collision_password_guards_and_disabled_user(self):
		user = self.make_user()
		key = frappe.get_doc(dict(doctype="Instance Key", user=user.name)).insert()
		self.assertEqual(len(key.api_key), 15)
		self.assertEqual(len(key.get_password("api_secret")), 15)
		self.assertEqual(key.name, key.api_key)
		self.assertTrue(set(key.api_secret) == {"*"})
		from arab_streaming.arab_streaming.doctype.instance_key.instance_key import generate_token

		fresh = generate_token()
		with patch(
			"arab_streaming.arab_streaming.doctype.instance_key.instance_key.generate_token",
			side_effect=[key.api_key, fresh, generate_token()],
		):
			second = frappe.get_doc(dict(doctype="Instance Key", user=user.name)).insert()
		self.assertEqual(second.api_key, fresh)
		manager = self.make_user(manager=True)
		frappe.set_user(manager.name)
		from frappe.client import get_password

		for operation in (
			lambda: key.reveal_secret(),
			lambda: get_password("Instance Key", key.name, "api_secret"),
			lambda: frappe.get_doc(dict(doctype="Instance Key", user=user.name)).insert(),
			lambda: key.delete(),
		):
			with self.assertRaises(frappe.PermissionError):
				operation()
		frappe.set_user("Administrator")
		frappe.db.set_value("User", user.name, "enabled", 0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(dict(doctype="Instance Key", user=user.name)).insert()

	def test_initial_child_progress_cannot_be_edited(self):
		doc = self.make_master()
		doc.append(
			"initial_sync_doctypes",
			{"doctype_name": "UOM", "mandatory": 1, "synced": 1, "document_count": 99},
		)
		doc.insert()
		self.assertEqual(doc.initial_sync_doctypes[0].synced, 0)
		self.assertEqual(doc.initial_sync_doctypes[0].mandatory, 0)
		doc.initial_sync_doctypes[0].synced = 1
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_enabled_peer_prevents_key_deletion(self):
		user = self.make_user()
		key = frappe.get_doc(dict(doctype="Instance Key", user=user.name)).insert()
		branch = frappe.get_doc(
			dict(doctype="Branch", branch="Key guard " + frappe.generate_hash(length=8))
		).insert()
		frappe.conf.arab_streaming_site_role = "Master"
		with system_write():
			peer = frappe.get_doc(
				dict(
					doctype="Slave Instance",
					restaurant_branch=branch.name,
					api_key=key.api_key,
					connection_status="Disconnected",
				)
			).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			key.delete()
		with system_write():
			peer.enabled = 0
			peer.save(ignore_permissions=True)
		key.delete()
		self.assertFalse(frappe.db.exists("Instance Key", key.name))

	def test_default_rows_and_initial_configuration_resets(self):
		doc = self.make_master().insert()
		self.assertEqual([r.doctype_name for r in doc.initial_sync_doctypes], ["UOM", "Item Group"])
		self.assertTrue(all(r.mandatory for r in doc.initial_sync_doctypes))
		self.assertFalse(doc.syncing_doctypes)
		with system_write():
			doc.syncing_status = "Failed"
			doc.progress = 40
			doc.initial_sync_doctypes[0].synced = 1
			doc.save()
		doc.initial_sync_doctypes[0].ignore_links = 1
		doc.save()
		self.assertEqual(doc.syncing_status, "Not Started")
		self.assertEqual(doc.progress, 0)
		self.assertEqual(doc.initial_sync_doctypes[0].synced, 0)
		with system_write():
			doc.syncing_status = "Complete"
			doc.save()
		doc.initial_sync_doctypes[0].skip_if_exists = 1
		doc.save()
		self.assertEqual(doc.syncing_status, "Complete")

	def test_extension_defaults_are_validated_and_do_not_grant_state_fields(self):
		with patch("frappe.get_hooks", return_value=["fake.provider"]), patch("frappe.get_attr") as provider:
			provider.return_value.return_value = [{"doctype_name": "Item", "mandatory": 1}]
			self.assertEqual(get_default_rows("initial", "Slave")[-1]["doctype_name"], "Item")
			provider.return_value.return_value = [{"doctype_name": "UOM"}]
			with self.assertRaises(frappe.ValidationError):
				get_default_rows("initial", "Slave")
			provider.return_value.return_value = [{"doctype_name": "Item", "synced": 1}]
			with self.assertRaises(frappe.ValidationError):
				get_default_rows("initial", "Slave")

	def test_service_cannot_rewrite_historical_payload_or_destinations(self):
		peer = self.make_master().insert()
		with system_write():
			event = frappe.get_doc(
				dict(
					doctype="Dispatch Event",
					reference_doctype="UOM",
					reference_name="test-uom",
					event_type="Create",
					event_sequence=2**53 + 1,
					created_on="2026-10-27 12:00:00",
					data='{"uom_name":"test-uom"}',
					instances=[{"instance_type": "Master Instance", "instance_name": peer.name}],
				)
			).insert(ignore_permissions=True)
			event.instances[0].synced = 1
			event.save(ignore_permissions=True)
			self.assertEqual(event.status, "Synced")
			event.data = '{"uom_name":"changed"}'
			with self.assertRaises(frappe.ValidationError):
				event.save(ignore_permissions=True)
			event.reload()
			event.instances = []
			with self.assertRaises(frappe.ValidationError):
				event.save(ignore_permissions=True)
