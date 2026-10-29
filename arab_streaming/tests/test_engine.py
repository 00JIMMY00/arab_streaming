import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.streaming.conditions import ConditionError, filters, matches, parse
from arab_streaming.streaming.guards import system_write


class TestConditions(unittest.TestCase):
	def test_predicate_and_query_share_comparisons(self):
		parsed = parse(
			'doc.status not in ["Complete", "Canceled"] and doc.docstatus == 1', {"status", "docstatus"}
		)
		self.assertEqual(
			filters(parsed), [["status", "not in", ["Complete", "Canceled"]], ["docstatus", "=", 1]]
		)
		self.assertTrue(matches(parsed, {"status": "Open", "docstatus": 1}))
		self.assertFalse(matches(parsed, {"status": "Complete", "docstatus": 1}))
		self.assertFalse(matches(parsed, {"status": "Open", "docstatus": 0}))

	def test_executable_and_unbounded_conditions_fail(self):
		for expression in (
			'__import__("os")',
			'doc.name.startswith("x")',
			"doc.__class__ == 1",
			"doc.name == other.name",
			"[x for x in doc.name]",
			'doc.name == "x" or doc.name == "y"',
			"doc.name == " + repr("x" * 5000),
		):
			with self.assertRaises(ConditionError):
				parse(expression, {"name"})


class TestOutboxTransactions(FrappeTestCase):
	def setUp(self):
		self.previous_user = frappe.session.user
		self.old_role = frappe.conf.get("arab_streaming_site_role")
		self.old_enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.conf.arab_streaming_enabled = True
		branch = frappe.get_doc(
			dict(doctype="Branch", branch="Outbox " + frappe.generate_hash(length=8))
		).insert()
		with system_write():
			peer = frappe.get_doc(
				dict(
					doctype="Slave Instance",
					restaurant_branch=branch.name,
					api_key="fake",
					connection_status="Disconnected",
					syncing_doctypes=[{"doctype_name": "UOM", "allow_creation": 1, "allow_local_rename": 1}],
				)
			).insert(ignore_permissions=True)
			frappe.get_doc(
				dict(
					doctype="Streaming Peer State",
					instance_type="Slave Instance",
					instance_name=peer.name,
					remote_installation_id=str(uuid.uuid4()),
					pairing_id=str(uuid.uuid4()),
					pairing_generation=1,
				)
			).insert(ignore_permissions=True)
		self.peer = peer
		self.state = frappe.get_doc(
			"Streaming Peer State", frappe.db.get_value("Streaming Peer State", {"instance_name": peer.name})
		)
		self.branch_name = branch.name
		frappe.db.commit()
		frappe.db.savepoint("outbox_test")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="outbox_test")
		with system_write():
			frappe.delete_doc("Streaming Peer State", self.state.name, ignore_permissions=True)
			frappe.delete_doc("Slave Instance", self.peer.name, ignore_permissions=True)
		frappe.delete_doc("Branch", self.branch_name)
		frappe.db.commit()
		frappe.conf.arab_streaming_site_role = self.old_role
		frappe.conf.arab_streaming_enabled = self.old_enabled
		frappe.set_user(self.previous_user)

	def test_insert_once_update_and_rollback_are_atomic(self):
		frappe.db.savepoint("business_save")
		name = "Streaming transaction " + frappe.generate_hash(length=8)
		doc = frappe.get_doc(dict(doctype="UOM", uom_name=name, must_be_whole_number=0)).insert()
		rows = frappe.get_all(
			"Dispatch Event",
			filters={"reference_doctype": "UOM", "reference_name": name},
			fields=["name", "event_type"],
		)
		self.assertEqual([row.event_type for row in rows], ["Create"])
		event = frappe.get_doc("Dispatch Event", rows[0].name)
		self.assertEqual(sum(row.instance_name == self.peer.name for row in event.instances), 1)
		self.assertEqual(json.loads(event.data)["document"]["name"], name)
		doc.must_be_whole_number = 1
		doc.save()
		self.assertEqual(frappe.db.count("Dispatch Event", {"reference_name": name}), 2)
		frappe.db.rollback(save_point="business_save")
		self.assertFalse(frappe.db.exists("UOM", name))
		self.assertFalse(frappe.db.exists("Dispatch Event", {"reference_name": name}))

	def test_receipt_is_idempotent_and_utc_datetime_is_database_safe(self):
		from arab_streaming.protocol.envelope import payload_digest
		from arab_streaming.streaming.inbox import ingest

		name = "Receipt test " + frappe.generate_hash(length=8)
		envelope = dict(
			protocol="arab-erp.streaming",
			version="1.0",
			source_installation_id=self.state.remote_installation_id,
			pairing_id=self.state.pairing_id,
			pairing_generation="1",
			event_id="test-event",
			event_sequence=str(2**53 + 1),
			created_on="2026-10-27T14:00:00+00:00",
			created_by="remote@example.invalid",
			reference_doctype="UOM",
			reference_name=name,
			event_type="Create",
			document_event="after_insert",
			metadata={},
			doctype_config={"doctype_name": "UOM", "priority": 0, "on_duplicated_insert": None},
			data={
				"document": {
					"doctype": "UOM",
					"name": name,
					"docstatus": 0,
					"uom_name": name,
					"enabled": 1,
					"must_be_whole_number": 0,
				}
			},
		)
		envelope["payload_digest"] = payload_digest(envelope)
		first = ingest(self.peer, self.state, [envelope])
		second = ingest(self.peer, self.state, [envelope])
		self.assertEqual(first, second)
		capture = frappe.get_doc("Capture Event", first[0]["capture_event"])
		self.assertEqual(capture.status, "Pending")
		self.assertEqual(capture.priority, 0)
		self.assertEqual(capture.event_sequence, 2**53 + 1)
		self.assertEqual(capture.created_on, frappe.utils.get_datetime("2026-10-27 14:00:00"))
		self.assertEqual(frappe.db.count("Streaming Event Receipt", {"pairing_id": self.state.pairing_id}), 1)
		envelope["data"]["document"]["must_be_whole_number"] = 1
		envelope["payload_digest"] = payload_digest(envelope)
		with self.assertRaises(frappe.ValidationError):
			ingest(self.peer, self.state, [envelope])
		self.assertFalse(frappe.db.exists("UOM", name))

	def test_receipt_holes_do_not_lose_earlier_events_and_restore_is_rejected(self):
		from arab_streaming.protocol.envelope import payload_digest
		from arab_streaming.streaming.inbox import ingest

		def item(sequence):
			name = "Receipt hole " + str(sequence)
			result = dict(
				protocol="arab-erp.streaming",
				version="1.0",
				source_installation_id=self.state.remote_installation_id,
				pairing_id=self.state.pairing_id,
				pairing_generation="1",
				event_id="hole-" + str(sequence),
				event_sequence=str(sequence),
				created_on="2026-10-27T14:00:00+00:00",
				created_by="remote@example.invalid",
				reference_doctype="UOM",
				reference_name=name,
				event_type="Create",
				document_event="after_insert",
				metadata={},
				doctype_config={"doctype_name": "UOM", "priority": 100},
				data={
					"document": {
						"doctype": "UOM",
						"name": name,
						"docstatus": 0,
						"uom_name": name,
						"enabled": 1,
						"must_be_whole_number": 0,
					}
				},
			)
			result["payload_digest"] = payload_digest(result)
			return result

		for seq in [100, 98, 99]:
			ingest(self.peer, self.state, [item(seq)], source_head="100")
		self.assertEqual(frappe.db.count("Streaming Event Receipt", {"pairing_id": self.state.pairing_id}), 3)
		self.assertEqual(
			frappe.db.get_value("Streaming Peer State", self.state.name, "received_through"), 100
		)
		for bad_head in ["99", "100.1", True, str(2**63)]:
			with self.assertRaises(frappe.ValidationError):
				ingest(self.peer, self.state, [item(101)], source_head=bad_head)
