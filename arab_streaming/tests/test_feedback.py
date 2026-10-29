import json
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.streaming.feedback import capture_for, receiver_results, request_retry
from arab_streaming.streaming.guards import system_write
from arab_streaming.streaming.status import dispatch_status


class TestReceiverFeedback(FrappeTestCase):
	def setUp(self):
		self.user = frappe.session.user
		self.role = frappe.conf.get("arab_streaming_site_role")
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_site_role = "Master"
		frappe.conf.arab_streaming_enabled = False
		frappe.db.savepoint("receiver_feedback")
		branch = frappe.get_doc(
			dict(doctype="Branch", branch="Feedback " + frappe.generate_hash(length=8))
		).insert()
		with system_write():
			self.peer = frappe.get_doc(
				dict(doctype="Slave Instance", restaurant_branch=branch.name, api_key="fake", enabled=1)
			).insert(ignore_permissions=True)
			self.state = frappe.get_doc(
				dict(
					doctype="Streaming Peer State",
					instance_type=self.peer.doctype,
					instance_name=self.peer.name,
					remote_installation_id=str(uuid.uuid4()),
					pairing_id=str(uuid.uuid4()),
					pairing_generation=1,
				)
			).insert(ignore_permissions=True)
			self.capture = frappe.get_doc(
				dict(
					doctype="Capture Event",
					instance_type=self.peer.doctype,
					instance_name=self.peer.name,
					reference_doctype="UOM",
					reference_name="Feedback UOM",
					event_type="Create",
					status="Failed",
					exception="Branch mismatch",
					data='{"document": {}}',
					doctype_config="{}",
					remote_dispatch_event="feedback-event",
					source_installation_id=self.state.remote_installation_id,
					pairing_id=self.state.pairing_id,
					pairing_generation=1,
					payload_digest="a" * 64,
					retries=3,
					next_attempt_at=frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=5),
				)
			).insert(ignore_permissions=True)
			self.receipt = frappe.get_doc(
				dict(
					doctype="Streaming Event Receipt",
					source_installation_id=self.state.remote_installation_id,
					pairing_id=self.state.pairing_id,
					pairing_generation=1,
					remote_dispatch_event="feedback-event",
					payload_digest="a" * 64,
					capture_event=self.capture.name,
				)
			).insert(ignore_permissions=True)
		self.events = [dict(event_id="feedback-event", payload_digest="a" * 64)]

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="receiver_feedback")
		frappe.conf.arab_streaming_site_role = self.role
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.set_user(self.user)

	def test_delivery_is_not_application_and_mixed_failures_stay_red(self):
		self.assertEqual(dispatch_status([{"synced": 1}]), "Delivered")
		self.assertEqual(dispatch_status([{"synced": 1, "receiver_status": "Success"}]), "Applied")
		self.assertEqual(
			dispatch_status(
				[{"synced": 1, "receiver_status": "Success"}, {"synced": 1, "receiver_status": "Failed"}]
			),
			"Failed",
		)
		self.assertEqual(
			dispatch_status(
				[{"synced": 1, "receiver_status": "Success"}, {"synced": 1, "receiver_status": "Skipped"}]
			),
			"Completed with Skips",
		)
		self.assertEqual(dispatch_status([{"exception": "unreachable"}]), "Delivery Failed")

	def test_results_and_retry_are_peer_bound_and_reuse_capture(self):
		result = receiver_results(self.peer, self.events)["results"][0]
		self.assertEqual(
			(result["status"], result["exception"], result["retries"]), ("Failed", "Branch mismatch", 3)
		)
		for event in [
			dict(event_id="another-event", payload_digest="a" * 64),
			dict(event_id="feedback-event", payload_digest="b" * 64),
		]:
			with self.assertRaises(frappe.PermissionError):
				receiver_results(self.peer, [event])
		old_data = self.capture.data
		with patch("frappe.enqueue") as enqueue:
			result = request_retry(self.capture)
			self.assertTrue(result["queued"])
			self.assertEqual(enqueue.call_args.kwargs["only_peer"], self.peer.name)
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
		self.capture.reload()
		self.assertEqual(self.capture.status, "Pending")
		self.assertIsNone(self.capture.next_attempt_at)
		self.assertEqual(self.capture.retries, 3)
		self.assertEqual(self.capture.data, old_data)
		self.assertEqual(
			frappe.db.count(
				"Capture Event",
				{"remote_dispatch_event": "feedback-event", "pairing_id": self.state.pairing_id},
			),
			1,
		)
		with self.assertRaises(frappe.ValidationError):
			request_retry(self.capture)
		# A valid receipt cannot be used to access an unrelated capture.
		frappe.db.set_value("Capture Event", self.capture.name, "pairing_generation", 2)
		with self.assertRaises(frappe.PermissionError):
			capture_for(self.peer, self.receipt)

	def test_terminal_result_survives_capture_cleanup_without_guessing(self):
		with system_write():
			self.capture.status = "Success"
			self.capture.save(ignore_permissions=True)
			self.assertEqual(
				frappe.db.get_value("Streaming Event Receipt", self.receipt.name, "application_status"),
				"Success",
			)
			frappe.delete_doc("Capture Event", self.capture.name, ignore_permissions=True)
		self.assertEqual(receiver_results(self.peer, self.events)["results"][0]["status"], "Success")
		frappe.db.set_value("Streaming Event Receipt", self.receipt.name, "application_status", None)
		self.assertEqual(receiver_results(self.peer, self.events)["results"][0]["status"], "Unavailable")

	def test_local_retry_requires_system_manager(self):
		from arab_streaming.api.actions import retry_capture

		frappe.set_user("Guest")
		previous = frappe.flags.in_test
		try:
			frappe.flags.in_test = False
			with self.assertRaises(frappe.PermissionError):
				retry_capture(self.capture.name)
		finally:
			frappe.flags.in_test = previous

	def test_sender_reflects_failure_and_retry_then_application_without_redelivery(self):
		from arab_streaming.api.actions import retry_dispatch
		from arab_streaming.api.events import retry
		from arab_streaming.streaming.feedback import refresh_results
		from arab_streaming.streaming.transport import acknowledge_for, envelope, pending_for

		with system_write():
			event = frappe.get_doc(
				dict(
					doctype="Dispatch Event",
					reference_doctype="UOM",
					reference_name="Feedback UOM",
					event_type="Create",
					document_event="after_insert",
					event_sequence=99,
					created_on=frappe.utils.now_datetime(),
					created_by="Administrator",
					data=json.dumps(
						{
							"document": {
								"doctype": "UOM",
								"name": "Feedback UOM",
								"uom_name": "Feedback UOM",
								"docstatus": 0,
							}
						}
					),
					metadata={},
					instances=[
						dict(
							instance_type=self.peer.doctype,
							instance_name=self.peer.name,
							pairing_id=self.state.pairing_id,
							pairing_generation=1,
							doctype_config='{"doctype_name":"UOM"}',
						)
					],
				)
			).insert(ignore_permissions=True)
		checksum = envelope(event, event.instances[0])["payload_digest"]
		frappe.db.set_value(
			"Streaming Event Receipt",
			self.receipt.name,
			{"remote_dispatch_event": event.name, "payload_digest": checksum},
		)
		frappe.db.set_value(
			"Capture Event",
			self.capture.name,
			{"remote_dispatch_event": event.name, "payload_digest": checksum},
		)
		acknowledge_for(
			self.peer, [dict(event_id=event.name, payload_digest=checksum, capture_event=self.capture.name)]
		)
		event.reload()
		self.assertEqual(event.status, "Delivered")
		self.assertNotIn(event.name, [e["event_id"] for e in pending_for(self.peer)])

		def wire(peer, method, **values):
			if method.endswith(".results"):
				return receiver_results(peer, values["events"])
			if method.endswith(".retry"):
				with patch("arab_streaming.api.events.addressed", return_value=(peer, self.state)):
					return retry(protocol="arab-erp.streaming", version="1.0", **values)
			self.fail("Unexpected endpoint " + method)

		with patch("arab_streaming.streaming.client.remote_call", side_effect=wire), patch("frappe.enqueue"):
			refresh_results(self.peer, event.name)
			event.reload()
			self.assertEqual(event.status, "Failed")
			self.assertEqual(event.instances[0].receiver_exception, "Branch mismatch")
			result = retry_dispatch(event.name, event.instances[0].name)
			self.assertEqual(result["capture_event"], self.capture.name)
			event.reload()
			self.assertEqual(event.status, "Delivered")
			self.assertEqual(event.instances[0].receiver_status, "Pending")
			self.capture.reload()
			with system_write():
				self.capture.status = "Success"
				self.capture.exception = None
				self.capture.save(ignore_permissions=True)
			refresh_results(self.peer, event.name)
			event.reload()
			self.assertEqual(event.status, "Applied")
			self.assertTrue(event.instances[0].synced)
			self.assertFalse(event.instances[0].receiver_exception)

	def test_remote_validation_error_is_visible_without_traceback(self):
		from arab_streaming.streaming.client import RemoteError, remote_call

		peer = frappe._dict(doctype="Master Instance", domain="https://example.invalid", access_key="x" * 15)
		peer.get_password = lambda field: "y" * 15
		response = frappe._dict(status_code=403)
		payload = json.dumps(
			dict(
				exc_type="PermissionError",
				exception="frappe.exceptions.PermissionError: Document Branch does not match the authenticated peer",
				exc="PRIVATE TRACEBACK",
			)
		)
		from unittest.mock import MagicMock

		with patch("arab_streaming.streaming.client.requests.post") as post:
			context = MagicMock()
			context.status_code = response.status_code
			context.iter_content.return_value = [payload.encode()]
			post.return_value.__enter__.return_value = context
			with self.assertRaises(RemoteError) as caught:
				remote_call(peer, "arab_streaming.api.events.results")
			self.assertIn("Document Branch does not match", str(caught.exception))
			self.assertNotIn("PRIVATE TRACEBACK", str(caught.exception))
