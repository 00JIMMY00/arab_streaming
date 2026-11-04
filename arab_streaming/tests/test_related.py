import copy
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming import related
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.dependencies import freeze
from arab_streaming.streaming.executor import apply_capture, apply_document
from arab_streaming.streaming.permissions import provision


class TestRelatedRecords(FrappeTestCase):
	def setUp(self):
		self.user = frappe.session.user
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_enabled = False
		self.rule = frappe._dict(
			name="test-rule",
			doctype_name="Customer",
			enabled=1,
			scope="Shared",
			direction="Both",
			revision=1,
			conditions="",
		)
		self.peer = frappe._dict(doctype="Master Instance", name=None, restaurant_branch="test-branch")
		self.executor = frappe.get_doc(
			dict(
				doctype="User",
				email="linked-executor-" + frappe.generate_hash(length=8) + "@example.invalid",
				first_name="Linked executor",
				send_welcome_email=0,
			)
		).insert()
		provision(self.executor.name, [dict(doctype_name="Customer", enabled=1)])
		self.customer = self.make_customer()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.set_user(self.user)
		frappe.local.streaming_current_sequence = None

	def make_customer(self):
		return frappe.get_doc(
			dict(
				doctype="Customer",
				customer_name="LINK-" + frappe.generate_hash(length=8),
				customer_type="Individual",
				customer_group="",
				territory="",
			)
		).insert()

	def make_contact(self, owners=None):
		return frappe.get_doc(
			dict(
				doctype="Contact",
				first_name="Contact-" + frappe.generate_hash(length=8),
				links=[
					dict(link_doctype="Customer", link_name=name) for name in owners or [self.customer.name]
				],
				phone_nos=[dict(phone="01012345678", is_primary_mobile_no=1)],
			)
		).insert()

	def make_address(self):
		return frappe.get_doc(
			dict(
				doctype="Address",
				address_title="Address-" + frappe.generate_hash(length=8),
				address_type="Billing",
				address_line1="Test street",
				city="Cairo",
				country="Egypt",
				links=[dict(link_doctype="Customer", link_name=self.customer.name)],
			)
		).insert()

	def rule_for(self, peer, doctype, *args, **kwargs):
		return self.rule if doctype == "Customer" else None

	def context(self, records=None, change=None, roots=None):
		result = dict(document=snapshot(self.customer), related_roots=roots or [snapshot(self.customer)])
		if change:
			result.update(related_only=1, related_change=change)
		else:
			result["related_records"] = records or []
		return result

	def run_related(self, data):
		with (
			patch.object(related, "config_for", side_effect=self.rule_for),
			replay("linked-test", self.executor.name),
		):
			return related.apply(self.peer, data)

	def remove_fixture(self, doc):
		for field in doc.meta.get_table_fields():
			frappe.db.delete(field.options, {"parent": doc.name, "parenttype": doc.doctype})
		frappe.db.delete(doc.doctype, {"name": doc.name})

	def test_bundle_includes_multiple_nonprimary_records_and_frozen_versions(self):
		contacts = [self.make_contact(), self.make_contact()]
		addresses = [self.make_address(), self.make_address()]
		with patch.object(related, "config_for", side_effect=self.rule_for):
			bundle = related.bundle(self.peer, snapshot(self.customer))
		self.assertEqual(len(bundle["related_records"]), 4)
		versions = freeze(snapshot(self.customer), bundle["related_records"])
		self.assertTrue({(d.doctype, d.name) for d in contacts + addresses} <= {v[:2] for v in versions})
		for doc in contacts + addresses:
			self.remove_fixture(doc)
		self.run_related(dict(document=snapshot(self.customer), **bundle))
		self.assertEqual(
			frappe.db.count("Dynamic Link", {"link_name": self.customer.name, "link_doctype": "Customer"}), 4
		)

	def test_update_phone_address_and_unlink_without_customer_write(self):
		for doc, field, value in [
			(self.make_contact(), "first_name", "Edited contact"),
			(self.make_address(), "address_line1", "Updated street"),
		]:
			before = snapshot(doc)
			after = copy.deepcopy(before)
			after[field] = value
			modified = frappe.db.get_value("Customer", self.customer.name, "modified")
			self.run_related(self.context(change=dict(operation="Update", document=after, before=before)))
			self.assertEqual(frappe.db.get_value(doc.doctype, doc.name, field), value)
			self.assertEqual(frappe.db.get_value("Customer", self.customer.name, "modified"), modified)
			before = snapshot(frappe.get_doc(doc.doctype, doc.name))
			after = dict(before, links=[])
			self.run_related(self.context(change=dict(operation="Update", document=after, before=before)))
			self.assertEqual(frappe.get_doc(doc.doctype, doc.name).links, [])

	def test_delete_and_repeat_delete_are_idempotent(self):
		for doc in [self.make_contact(), self.make_address()]:
			document = snapshot(doc)
			data = self.context(change=dict(operation="Delete", document=document, before=document))
			self.run_related(data)
			self.assertFalse(frappe.db.exists(doc.doctype, doc.name))
			self.run_related(data)

	def test_rename_keeps_dynamic_links(self):
		doc = self.make_contact()
		after = dict(snapshot(doc), name="Renamed-" + frappe.generate_hash(length=8))
		data = self.context(
			change=dict(
				operation="Rename", document=after, old_name=doc.name, new_name=after["name"], merge=False
			)
		)
		self.run_related(data)
		self.assertFalse(frappe.db.exists("Contact", doc.name))
		self.assertEqual(frappe.get_doc("Contact", after["name"]).links[0].link_name, self.customer.name)
		self.run_related(data)

	def test_owner_save_does_not_overwrite_existing_contact(self):
		doc = self.make_contact()
		old = snapshot(doc)
		doc.first_name = "Local edit"
		doc.save()
		self.run_related(self.context(records=[old]))
		self.assertEqual(frappe.db.get_value("Contact", doc.name, "first_name"), "Local edit")

	def test_local_conflict_blocks_linked_update(self):
		doc = self.make_contact()
		before = snapshot(doc)
		after = dict(before, first_name="Source edit")
		doc.first_name = "Independent local edit"
		doc.save()
		with self.assertRaises(frappe.ValidationError):
			self.run_related(self.context(change=dict(operation="Update", document=after, before=before)))
		self.assertEqual(frappe.db.get_value("Contact", doc.name, "first_name"), "Independent local edit")

	def test_cross_scope_links_are_projected_and_receiver_links_preserved(self):
		other = self.make_customer()
		doc = self.make_contact([self.customer.name, other.name])
		root = snapshot(self.customer)
		with patch.object(
			related, "permitted", side_effect=lambda p, d, *a, **kw: d["name"] == self.customer.name
		):
			bundle = related.bundle(self.peer, root)
		self.assertEqual(related.owners(bundle["related_records"][0]), {("Customer", self.customer.name)})
		after = dict(bundle["related_records"][0], first_name="Shared edit")
		self.run_related(self.context(change=dict(operation="Create", document=after)))
		self.assertEqual(
			related.owners(snapshot(frappe.get_doc("Contact", doc.name))),
			{("Customer", self.customer.name), ("Customer", other.name)},
		)

	def test_shared_delete_preserves_other_owner(self):
		other = self.make_customer()
		doc = self.make_contact([self.customer.name, other.name])
		document = snapshot(doc)
		# A complete base proves the receiver was not independently edited.
		with patch("arab_streaming.streaming.mapping.check_base"):
			self.run_related(
				self.context(
					change=dict(
						operation="Delete",
						document=related.project(document, {("Customer", self.customer.name)}),
					)
				)
			)
		self.assertEqual(
			related.owners(snapshot(frappe.get_doc("Contact", doc.name))), {("Customer", other.name)}
		)

	def test_wrong_direction_condition_or_unrelated_payload_is_rejected(self):
		doc = self.make_contact()
		data = self.context(records=[snapshot(doc)])
		with (
			patch.object(related, "config_for", return_value=None),
			self.assertRaises(frappe.PermissionError),
		):
			related.validate(self.peer, data)
		self.rule.conditions = "doc.customer_type == 'Company'"
		with self.assertRaises(frappe.PermissionError):
			self.run_related(data)
		self.rule.conditions = ""
		other = self.make_customer()
		data["related_records"][0]["links"][0]["link_name"] = other.name
		with self.assertRaises(frappe.PermissionError):
			self.run_related(data)

	def test_new_customer_primary_contact_and_address_cycle(self):
		contact, address = self.make_contact(), self.make_address()
		self.customer.customer_primary_contact = contact.name
		self.customer.customer_primary_address = address.name
		self.customer.save()
		root = snapshot(self.customer)
		manifest = {(doc.doctype, doc.name): snapshot(doc) for doc in [contact, address]}
		for doc in [contact, address, self.customer]:
			self.remove_fixture(doc)

		def remote(_peer, _method, **kwargs):
			return {"document": manifest[(kwargs["doctype"], kwargs["name"])]}

		with (
			patch("arab_streaming.streaming.client.remote_call", side_effect=remote),
			patch("arab_streaming.streaming.connections.wire_identity", return_value={}),
			replay("cycle", self.executor.name),
		):
			result = apply_document(self.peer, root)
		self.assertEqual(result[0], "Success")
		for dt, name in manifest:
			self.assertIn(
				("Customer", self.customer.name), related.owners(snapshot(frappe.get_doc(dt, name)))
			)
		self.assertEqual(
			frappe.db.get_value("Customer", self.customer.name, "customer_primary_contact"), contact.name
		)

	def test_back_reference_does_not_check_its_own_pending_capture(self):
		contact = self.make_contact()
		document = snapshot(contact)
		self.remove_fixture(contact)
		peer = frappe._dict(doctype="Master Instance", name="mock-peer")
		original = frappe.db.exists

		def exists(dt, *args, **kwargs):
			if dt == "Capture Event" and args[0].get("reference_doctype") == "Customer":
				self.fail("Ancestor back-reference must be skipped before pending-event lookup")
			return original(dt, *args, **kwargs)

		with patch.object(frappe.db, "exists", side_effect=exists), replay("cycle", self.executor.name):
			apply_document(peer, document, seen={("Customer", self.customer.name)})

	def test_late_contact_capture_uses_old_owner_on_unlink_and_deduplicates(self):
		doc = self.make_contact()
		before = copy.deepcopy(doc)
		doc.links = []
		doc._doc_before_save = before
		peer = frappe._dict(doctype="Slave Instance", name="test-peer", restaurant_branch="test-branch")
		state = frappe._dict(name="test-state")
		with (
			patch("arab_streaming.streaming.outbox.recipients", return_value=[(peer, state, self.rule)]),
			patch("arab_streaming.streaming.outbox.store_event") as store,
		):
			related.capture_change(doc, "on_update", set())
			self.assertEqual(store.call_count, 1)
			data = store.call_args.kwargs["data"]
			self.assertEqual(data["related_change"]["document"]["links"], [])
			self.assertEqual(len(data["related_change"]["before"]["links"]), 1)
			related.capture_change(doc, "on_update", {("Slave Instance", "test-peer")})
			self.assertEqual(store.call_count, 1)

	def test_apply_capture_updates_link_without_saving_customer_or_echo(self):
		doc = self.make_contact()
		before = snapshot(doc)
		after = dict(before, first_name="Captured contact edit")
		capture = frappe._dict(
			data=frappe.as_json(self.context(change=dict(operation="Update", document=after, before=before))),
			doctype_config=frappe.as_json(dict(rule_id=self.rule.name)),
			metadata="{}",
			event_type="Update",
			reference_doctype="Customer",
			reference_name=self.customer.name,
			remote_dispatch_event="test-event",
			event_sequence=100,
		)
		state = frappe._dict(pairing_id="test-pair", execution_user=self.executor.name)
		modified = frappe.db.get_value("Customer", self.customer.name, "modified")
		with (
			patch("arab_streaming.streaming.executor.peer_state", return_value=state),
			patch("arab_streaming.streaming.policy.config_for", side_effect=self.rule_for),
			patch.object(related, "config_for", side_effect=self.rule_for),
			patch("arab_streaming.streaming.outbox.recipients") as routes,
		):
			frappe.conf.arab_streaming_enabled = True
			self.assertEqual(apply_capture(capture, self.peer)[0], "Success")
			routes.assert_not_called()
		self.assertEqual(frappe.db.get_value("Customer", self.customer.name, "modified"), modified)
		self.assertEqual(frappe.db.get_value("Contact", doc.name, "first_name"), "Captured contact edit")
		self.assertIsNone(frappe.local.streaming_current_sequence)

	def test_real_hooks_capture_later_add_edit_and_transaction_rollback(self):
		from arab_streaming.streaming.guards import system_write

		branch = frappe.get_doc(
			dict(doctype="Branch", branch="Related test " + frappe.generate_hash(length=8))
		).insert()
		with system_write():
			peer = frappe.get_doc(
				dict(
					doctype="Slave Instance",
					restaurant_branch=branch.name,
					api_key="fixture",
					connection_status="Disconnected",
				)
			).insert(ignore_permissions=True)
		state = frappe._dict(name="fixture-state", pairing_id="fixture-pair", pairing_generation=1)
		config = dict(
			doctype_name="Customer", rule_id="test-rule", rule_revision=1, on_duplicated_insert="Update"
		)

		def routes(doc, method, *args):
			return [(peer, state, config)] if doc.doctype == "Customer" else []

		with (
			patch("arab_streaming.streaming.outbox.recipients", side_effect=routes),
			patch("arab_streaming.streaming.outbox.fence"),
			patch.object(related, "config_for", side_effect=self.rule_for),
		):
			frappe.conf.arab_streaming_enabled = True
			frappe.db.savepoint("linked_business")
			doc = self.make_contact()
			rows = frappe.get_all(
				"Dispatch Event",
				filters={"reference_doctype": "Customer", "reference_name": self.customer.name},
				fields=["name", "data"],
				order_by="event_sequence",
			)
			self.assertEqual(len(rows), 1)
			self.assertEqual(frappe.parse_json(rows[0].data)["related_change"]["operation"], "Create")
			doc.first_name = "Later edit"
			doc.save()
			self.assertEqual(frappe.db.count("Dispatch Event", {"reference_name": self.customer.name}), 2)
			doc.reload()
			doc.save()
			self.assertEqual(frappe.db.count("Dispatch Event", {"reference_name": self.customer.name}), 2)
			self.customer.reload()
			self.customer.customer_primary_contact = doc.name
			self.customer.save()
			events = frappe.get_all(
				"Dispatch Event",
				filters={"reference_name": self.customer.name},
				fields=["name", "data"],
				order_by="event_sequence desc",
			)
			self.assertEqual(len(events), 3)
			self.assertEqual(len(frappe.parse_json(events[0].data)["related_records"]), 1)
			self.assertTrue(
				frappe.db.exists(
					"Streaming Event Dependency",
					{
						"dispatch_event": events[0].name,
						"reference_doctype": "Contact",
						"reference_name": doc.name,
					},
				)
			)
			frappe.db.rollback(save_point="linked_business")
			self.assertFalse(frappe.db.exists("Contact", doc.name))
			self.assertFalse(frappe.db.exists("Dispatch Event", {"reference_name": self.customer.name}))

	def test_parent_delete_clears_primary_links_before_cascaded_children(self):
		self.check_parent_delete(shared=False)

	def test_parent_delete_unlinks_shared_contact_and_preserves_other_customer(self):
		self.check_parent_delete(shared=True)

	def check_parent_delete(self, shared):
		from arab_streaming.streaming.guards import system_write
		from arab_streaming.streaming.mapping import remember

		branch = frappe.get_doc(
			dict(doctype="Branch", branch="Delete test " + frappe.generate_hash(length=8))
		).insert()
		with system_write():
			peer = frappe.get_doc(
				dict(
					doctype="Slave Instance",
					restaurant_branch=branch.name,
					api_key="fixture",
					connection_status="Disconnected",
				)
			).insert(ignore_permissions=True)
			frappe.get_doc(
				dict(
					doctype="Streaming Peer State",
					instance_type=peer.doctype,
					instance_name=peer.name,
					remote_installation_id=str(uuid.uuid4()),
					pairing_id=str(uuid.uuid4()),
					pairing_generation=1,
					execution_user=self.executor.name,
				)
			).insert(ignore_permissions=True)
		other = self.make_customer() if shared else None
		contact = self.make_contact([self.customer.name, other.name] if other else None)
		address = self.make_address()
		self.customer.customer_primary_contact = contact.name
		self.customer.customer_primary_address = address.name
		self.customer.save()
		root = snapshot(self.customer)
		remember(peer, root, self.customer)
		# Address's normal controller updates the owner's rendered primary_address
		# without an owner event; the linked event must checkpoint that write.
		before_address = snapshot(address)
		edited_address = dict(before_address, address_line1="Updated before owner deletion")
		from frappe.contacts.doctype.address.address import get_address_display

		fresh_root = snapshot(frappe.get_doc("Customer", self.customer.name))
		edited_root = dict(fresh_root, primary_address=get_address_display(edited_address))
		edit_capture = frappe._dict(
			data=frappe.as_json(
				dict(
					document=edited_root,
					related_roots=[edited_root],
					related_only=1,
					related_change=dict(operation="Update", document=edited_address, before=before_address),
				)
			),
			doctype_config=frappe.as_json(dict(rule_id="test-rule")),
			metadata="{}",
			event_type="Update",
			reference_doctype="Customer",
			reference_name=self.customer.name,
			remote_dispatch_event="edit-before-delete",
			event_sequence=1,
		)
		with (
			patch("arab_streaming.streaming.policy.config_for", side_effect=self.rule_for),
			patch.object(related, "config_for", side_effect=self.rule_for),
		):
			self.assertEqual(apply_capture(edit_capture, peer)[0], "Success")
		address.reload()
		self.customer.reload()
		root = snapshot(self.customer)
		contact_count = frappe.db.count("Contact")
		after = dict(root, customer_primary_contact=None, customer_primary_address=None)
		with (
			patch("arab_streaming.streaming.policy.config_for", side_effect=self.rule_for),
			patch.object(related, "config_for", side_effect=self.rule_for),
		):
			for index, doc in enumerate((contact, address), 1):
				document = snapshot(doc)
				preserve = shared and doc.doctype == "Contact"
				change = dict(operation="Delete", document=document, before=document)
				if preserve:
					change = dict(
						operation="Update",
						before=document,
						document=related.project(document, {("Customer", other.name)}),
					)
				data = dict(
					document=after,
					related_roots=[after] + ([snapshot(other)] if other else []),
					related_only=1,
					related_change=change,
				)
				capture = frappe._dict(
					data=frappe.as_json(data),
					doctype_config=frappe.as_json(dict(rule_id="test-rule")),
					metadata="{}",
					event_type="Update",
					reference_doctype="Customer",
					reference_name=self.customer.name,
					remote_dispatch_event="delete-child-" + str(index),
					event_sequence=index,
				)
				self.assertEqual(apply_capture(capture, peer)[0], "Success")
				self.assertEqual(bool(frappe.db.exists(doc.doctype, doc.name)), bool(preserve))
				if doc.doctype == "Contact":
					self.assertEqual(frappe.db.count("Contact"), contact_count - (0 if preserve else 1))
			capture.data = frappe.as_json(dict(document=after, base_digest=digest(after)))
			capture.event_type, capture.event_sequence = "Delete", 3
			self.assertEqual(apply_capture(capture, peer)[0], "Success")
		self.assertFalse(frappe.db.exists("Customer", self.customer.name))
		if other:
			self.assertEqual(
				related.owners(snapshot(frappe.get_doc("Contact", contact.name))), {("Customer", other.name)}
			)

	def test_dependency_guard_only_considers_earlier_events(self):
		contact = self.make_contact()
		root = dict(snapshot(self.customer), customer_primary_contact=contact.name)
		peer = frappe._dict(doctype="Master Instance", name="mock-peer")
		original = frappe.db.exists
		checked = []

		def exists(dt, *args, **kwargs):
			if dt == "Capture Event":
				self.assertEqual(args[0].get("event_sequence"), ["<", 10])
				checked.append(args[0]["reference_doctype"])
				return False
			return original(dt, *args, **kwargs)

		frappe.local.streaming_current_sequence = 10
		with (
			patch.object(frappe.db, "exists", side_effect=exists),
			replay("future-event", self.executor.name),
		):
			self.assertEqual(apply_document(peer, root)[0], "Success")
		self.assertIn("Contact", checked)
