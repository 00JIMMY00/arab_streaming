import json
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.streaming import copying
from arab_streaming.streaming.guards import system_write


class TestCopyExisting(FrappeTestCase):
	def setUp(self):
		self.user = frappe.session.user
		self.role = frappe.conf.get("arab_streaming_site_role")
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.conf.arab_streaming_enabled = False
		self.prefix = "Copy " + frappe.generate_hash(length=10)
		self.branch = frappe.get_doc(dict(doctype="Branch", branch=self.prefix)).insert()
		self.execution_user = frappe.get_doc(
			dict(
				doctype="User",
				email="copy-" + frappe.generate_hash(length=10) + "@example.invalid",
				first_name="Copy test",
				enabled=1,
				send_welcome_email=0,
			)
		).insert()
		self.other_rules = frappe.get_all(
			"Streaming Rule", filters={"doctype_name": "UOM", "enabled": 1}, pluck="name"
		)
		for name in self.other_rules:
			frappe.db.set_value("Streaming Rule", name, "enabled", 0)
		self.rule = frappe.get_doc(
			dict(
				doctype="Streaming Rule",
				doctype_name="UOM",
				enabled=1,
				direction="Both",
				scope="Shared",
				all_branches=0,
				branches=[dict(branch=self.branch.name)],
				conditions='doc.name in ["' + self.prefix + ' A", "' + self.prefix + ' B"]',
				on_duplicated_insert="Update",
			)
		).insert()
		with system_write():
			self.peer = frappe.get_doc(
				dict(
					doctype="Slave Instance",
					restaurant_branch=self.branch.name,
					domain="https://receiver.example.invalid",
					api_key="fake",
					enabled=1,
					connection_status="Connected",
				)
			).insert(ignore_permissions=True)
			self.state = frappe.get_doc(
				dict(
					doctype="Streaming Peer State",
					instance_type=self.peer.doctype,
					instance_name=self.peer.name,
					remote_installation_id=str(uuid.uuid4()),
					pairing_id=str(uuid.uuid4()),
					pairing_generation=1,
					execution_user=self.execution_user.name,
				)
			).insert(ignore_permissions=True)
		from arab_streaming.streaming.permissions import provision

		provision(self.execution_user.name, [dict(doctype_name="UOM", enabled=1)])
		for suffix in [" A", " B", " Excluded"]:
			frappe.get_doc(dict(doctype="UOM", uom_name=self.prefix + suffix)).insert()
		# Rule reads use the committed-configuration connection, as in real saves.
		frappe.db.commit()
		frappe.conf.arab_streaming_enabled = True

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_enabled = False
		frappe.db.rollback()
		with system_write():
			for event in frappe.get_all(
				"Dispatch Event", filters={"reference_name": ["like", self.prefix + "%"]}, pluck="name"
			):
				frappe.db.delete("Streaming Event Dependency", {"dispatch_event": event})
				frappe.delete_doc("Dispatch Event", event, ignore_permissions=True)
			for run in frappe.get_all("Streaming Copy Run", filters={"rule": self.rule.name}, pluck="name"):
				frappe.delete_doc("Streaming Copy Run", run, ignore_permissions=True)
			frappe.db.delete("Streaming Rule Revision", {"rule": self.rule.name})
			frappe.db.set_value("Streaming Rule", self.rule.name, "enabled", 0)
			frappe.delete_doc("Streaming Rule", self.rule.name, ignore_permissions=True)
			if hasattr(self, "reverse_peer"):
				frappe.delete_doc("Streaming Peer State", self.reverse_state.name, ignore_permissions=True)
				frappe.db.set_value(
					"Master Instance", self.reverse_peer.name, "connection_status", "Disconnected"
				)
				frappe.delete_doc("Master Instance", self.reverse_peer.name, ignore_permissions=True)
			frappe.delete_doc("Streaming Peer State", self.state.name, ignore_permissions=True)
			frappe.delete_doc("Slave Instance", self.peer.name, ignore_permissions=True)
		for name in frappe.get_all("UOM", filters={"name": ["like", self.prefix + "%"]}, pluck="name"):
			frappe.delete_doc("UOM", name, ignore_permissions=True)
		frappe.delete_doc("Branch", self.branch.name, ignore_permissions=True)
		frappe.delete_doc("User", self.execution_user.name, ignore_permissions=True)
		for name in self.other_rules:
			frappe.db.set_value("Streaming Rule", name, "enabled", 1)
		frappe.db.commit()
		frappe.conf.arab_streaming_site_role = self.role
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.set_user(self.user)

	def start(self, **values):
		result = copying.start_source(
			self.peer, self.rule.name, str(uuid.uuid4()), revision=self.rule.revision, **values
		)
		frappe.db.commit()
		return result["name"]

	def test_bounded_copy_preserves_source_and_can_repeat_after_completion(self):
		self.assertEqual(copying.preview_source(self.peer, self.rule.name)["count"], 2)
		before = frappe.db.get_value("UOM", self.prefix + " A", "modified")
		run = self.start()
		self.assertTrue(copying.step(self.peer, run))
		self.assertEqual(frappe.db.count("Dispatch Event", {"copy_run": run}), 1)
		self.assertEqual(frappe.db.get_value("Streaming Copy Run", run, "last_name"), self.prefix + " A")
		self.assertTrue(copying.step(self.peer, run))
		self.assertFalse(copying.step(self.peer, run))
		self.assertEqual(frappe.db.get_value("Streaming Copy Run", run, "status"), "Waiting")
		events = frappe.get_all(
			"Dispatch Event",
			filters={"copy_run": run},
			fields=["name", "reference_name", "metadata", "data"],
			order_by="event_sequence",
		)
		self.assertEqual([e.reference_name for e in events], [self.prefix + " A", self.prefix + " B"])
		self.assertEqual(frappe.parse_json(events[0].metadata)["preserve_existing"], 1)
		self.assertNotIn("dependencies", frappe.parse_json(events[0].data))
		self.assertEqual(frappe.db.get_value("UOM", self.prefix + " A", "modified"), before)
		with self.assertRaises(frappe.ValidationError):
			self.start()
		# These are fixture receiver outcomes, not fabricated business records.
		frappe.db.sql("UPDATE `tabDispatch Event` SET status='Skipped' WHERE copy_run=%s", (run,))
		frappe.db.sql(
			"UPDATE `tabDispatch Event Instance` dest JOIN `tabDispatch Event` event ON event.name=dest.parent SET dest.synced=1,dest.receiver_status='Skipped' WHERE event.copy_run=%s",
			(run,),
		)
		frappe.db.commit()
		copying.step(self.peer, run)
		result = frappe.get_doc("Streaming Copy Run", run)
		self.assertEqual((result.status, result.skipped_records, result.pending_records), ("Complete", 2, 0))
		second = self.start(existing_policy="Rule")
		self.assertNotEqual(second, run)
		copying.step(self.peer, second)
		self.assertEqual(frappe.db.count("Dispatch Event", {"copy_run": second}), 1)
		with (
			patch("frappe.get_all", return_value=["run-a", "run-b"]),
			patch("arab_streaming.streaming.copying.step", return_value=True) as step,
		):
			copying.advance(self.peer, maximum=5)
			self.assertEqual(step.call_count, 5)

	def test_request_idempotency_and_wrong_pair_rejected(self):
		request = str(uuid.uuid4())
		one = copying.start_source(self.peer, self.rule.name, request, revision=self.rule.revision)
		frappe.db.commit()
		two = copying.start_source(self.peer, self.rule.name, request, revision=self.rule.revision)
		self.assertEqual(one["name"], two["name"])
		self.assertEqual(frappe.db.count("Streaming Copy Run", {"request_id": request}), 1)
		frappe.db.set_value("Streaming Peer State", self.state.name, "pairing_generation", 2)
		with self.assertRaises(frappe.PermissionError):
			copying.authorized_run(self.peer, one["name"])

	def test_failed_item_rolls_back_and_resume_does_not_duplicate(self):
		run = self.start()
		with patch(
			"arab_streaming.streaming.outbox.store_event",
			side_effect=frappe.ValidationError("fixture failure"),
		):
			copying.advance(self.peer, maximum=1)
		doc = frappe.get_doc("Streaming Copy Run", run)
		self.assertEqual((doc.status, doc.queued_records), ("Failed", 0))
		self.assertFalse(doc.last_name)
		self.assertEqual(frappe.db.count("Dispatch Event", {"copy_run": run}), 0)
		copying.control_source(self.peer, run, "resume")
		frappe.db.commit()
		copying.advance(self.peer, maximum=1)
		self.assertEqual(frappe.db.count("Dispatch Event", {"copy_run": run}), 1)
		copying.control_source(self.peer, run, "stop")
		frappe.db.commit()
		copying.advance(self.peer)
		self.assertEqual(frappe.db.count("Dispatch Event", {"copy_run": run}), 1)

	def test_rule_change_and_existing_unresolved_event_are_not_bypassed(self):
		run = self.start()
		self.rule.conditions += " and doc.enabled == 1"
		self.rule.save()
		frappe.db.commit()
		copying.advance(self.peer)
		self.assertEqual(frappe.db.get_value("Streaming Copy Run", run, "status"), "Failed")
		self.assertIn("Rule changed", frappe.db.get_value("Streaming Copy Run", run, "exception"))
		copying.control_source(self.peer, run, "stop")
		frappe.db.commit()
		second = self.start()
		copying.step(self.peer, second)
		copying.control_source(self.peer, second, "stop")
		frappe.db.commit()
		third = self.start()
		copying.advance(self.peer)
		self.assertEqual(frappe.db.count("Dispatch Event", {"copy_run": third}), 0)
		self.assertIn(
			"Resolve existing Dispatch Event", frappe.db.get_value("Streaming Copy Run", third, "exception")
		)

	def test_skip_existing_avoids_all_file_and_dependency_transfers(self):
		from arab_streaming.protocol.codec import snapshot
		from arab_streaming.streaming.executor import apply_document

		document = snapshot(frappe.get_doc("UOM", self.prefix + " A"))
		with (
			patch(
				"arab_streaming.streaming.files.import_files",
				side_effect=AssertionError("must not download files"),
			),
			patch(
				"arab_streaming.streaming.protected.import_values",
				side_effect=AssertionError("must not fetch secrets"),
			),
		):
			status, _ = apply_document(self.peer, document, policy="Skip")
		self.assertEqual(status, "Skipped")

	def test_reverse_source_uses_same_worker_and_rule_direction_is_enforced(self):
		frappe.conf.arab_streaming_site_role = "Slave"
		with system_write():
			self.reverse_peer = frappe.get_doc(
				dict(
					doctype="Master Instance",
					domain="https://master.example.invalid",
					site_domain="https://slave.example.invalid",
					access_key="A" * 15,
					secret="B" * 15,
					execution_user=self.execution_user.name,
				)
			).insert(ignore_permissions=True)
			frappe.db.set_value(
				"Master Instance",
				self.reverse_peer.name,
				{"restaurant_branch": self.branch.name, "connection_status": "Connected"},
			)
			self.reverse_peer.reload()
			self.reverse_state = frappe.get_doc(
				dict(
					doctype="Streaming Peer State",
					instance_type="Master Instance",
					instance_name=self.reverse_peer.name,
					remote_installation_id=str(uuid.uuid4()),
					pairing_id=str(uuid.uuid4()),
					pairing_generation=1,
					execution_user=self.execution_user.name,
				)
			).insert(ignore_permissions=True)
		frappe.db.commit()
		self.assertEqual(copying.preview_source(self.reverse_peer, self.rule.name)["count"], 2)
		run = copying.start_source(
			self.reverse_peer, self.rule.name, str(uuid.uuid4()), revision=self.rule.revision
		)
		frappe.db.commit()
		self.assertEqual(run["direction"], "Slave to Master")
		copying.step(self.reverse_peer, run["name"])
		event = frappe.get_doc(
			"Dispatch Event", frappe.db.get_value("Dispatch Event", {"copy_run": run["name"]})
		)
		self.assertEqual(event.instances[0].instance_type, "Master Instance")
		frappe.conf.arab_streaming_site_role = "Master"
		self.rule.direction = "Slave to Master"
		self.rule.save()
		frappe.db.commit()
		with self.assertRaises(frappe.PermissionError):
			copying.preview_source(self.peer, self.rule.name)
