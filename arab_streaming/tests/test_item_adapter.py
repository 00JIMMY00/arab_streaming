import copy
import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.adapters import linked_records, validate_document
from arab_streaming.streaming.executor import apply_document


class TestItemAdapter(FrappeTestCase):
	def setUp(self):
		self.previous_user = frappe.session.user
		self.previous_enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_enabled = False
		frappe.db.savepoint("item_adapter_test")
		self.prefix = "Streaming Item " + frappe.generate_hash(length=8)
		if not frappe.db.exists("UOM", "Nos"):
			frappe.get_doc(dict(doctype="UOM", uom_name="Nos")).insert()
		if not frappe.db.exists("Item Group", "All Item Groups"):
			frappe.get_doc(dict(doctype="Item Group", item_group_name="All Item Groups", is_group=1)).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="item_adapter_test")
		frappe.conf.arab_streaming_enabled = self.previous_enabled
		frappe.set_user(self.previous_user)

	def test_catalog_create_dependencies_children_update_and_no_stock_or_price(self):
		stock = frappe.get_doc(dict(doctype="UOM", uom_name=self.prefix + " Unit")).insert()
		box = frappe.get_doc(dict(doctype="UOM", uom_name=self.prefix + " Box")).insert()
		group = frappe.get_doc(
			dict(
				doctype="Item Group",
				item_group_name=self.prefix + " Group",
				parent_item_group="All Item Groups",
			)
		).insert()
		brand = frappe.get_doc(dict(doctype="Brand", brand=self.prefix + " Brand")).insert()
		item = frappe.get_doc(
			dict(
				doctype="Item",
				item_code=self.prefix,
				item_name="Streaming اختبار",
				item_group=group.name,
				stock_uom=stock.name,
				brand=brand.name,
				is_stock_item=1,
				uoms=[
					{"uom": stock.name, "conversion_factor": 1},
					{"uom": box.name, "conversion_factor": 12},
				],
				barcodes=[{"barcode": self.prefix + "-barcode", "uom": box.name}],
			)
		).insert()
		# These values must never trigger Item.after_insert stock/price creation
		# at the receiver, even if present on the source Item.
		item.opening_stock, item.standard_rate, item.valuation_rate = 8, 42, 5
		document = snapshot(item)
		for field in ("opening_stock", "standard_rate", "valuation_rate", "default_bom"):
			self.assertNotIn(field, document)
		dependencies = {(doc.doctype, doc.name): snapshot(doc) for doc in (stock, box, group, brand)}
		self.assertIn(("UOM", box.name, "Item.uoms[2].uom"), list(linked_records(document)))
		frappe.delete_doc("Item", item.name)
		for doc in (brand, group, box, stock):
			frappe.delete_doc(doc.doctype, doc.name)
		price_count = frappe.db.count("Item Price")
		ledger_count = frappe.db.count("Stock Ledger Entry")

		def fetch(_peer, _method, **args):
			return {"document": dependencies[(args["doctype"], args["name"])]}

		with (
			patch("arab_streaming.streaming.connections.wire_identity", return_value={}),
			patch("arab_streaming.streaming.client.remote_call", side_effect=fetch) as remote,
		):
			status, _ = apply_document(frappe._dict(doctype="Master Instance"), document)
			self.assertEqual(status, "Success")
			self.assertEqual(remote.call_count, 4)
		actual = frappe.get_doc("Item", item.name)
		self.assertEqual(actual.item_name, "Streaming اختبار")
		self.assertEqual(
			[(r.uom, r.conversion_factor) for r in actual.uoms], [(stock.name, 1), (box.name, 12)]
		)
		self.assertEqual(actual.barcodes[0].barcode, document["barcodes"][0]["barcode"])
		self.assertEqual([r.name for r in actual.uoms], [r["name"] for r in document["uoms"]])
		self.assertEqual(frappe.db.count("Item Price"), price_count)
		self.assertEqual(frappe.db.count("Stock Ledger Entry"), ledger_count)
		self.assertEqual(actual.opening_stock or 0, 0)
		actual_snapshot = snapshot(actual)
		self.assertEqual(
			{
				key: (document[key], actual_snapshot[key])
				for key in document
				if document[key] != actual_snapshot[key]
			},
			{},
		)
		self.assertEqual(digest(actual_snapshot), digest(document))
		updated = copy.deepcopy(document)
		updated["item_name"] = "Streaming Updated"
		updated["uoms"][1]["conversion_factor"] = 24
		apply_document(frappe._dict(doctype="Master Instance"), updated, base_digest=digest(document))
		self.assertEqual(frappe.get_doc("Item", item.name).uoms[1].conversion_factor, 24)
		self.assertEqual(frappe.get_doc("Item", item.name).item_name, "Streaming Updated")

	def test_bad_children_local_fields_and_attachments_fail_before_write(self):
		doc = frappe.get_doc(
			dict(
				doctype="Item",
				name=self.prefix,
				item_code=self.prefix,
				stock_uom="Nos",
				item_group="All Item Groups",
			)
		)
		document = snapshot(doc)
		for key, value in (("opening_stock", 10), ("flags", {"ignore_permissions": True})):
			bad = dict(document, **{key: value})
			with self.assertRaises(frappe.ValidationError):
				validate_document(bad)
		bad = copy.deepcopy(document)
		bad["uoms"] = [{"doctype": "User", "name": "Administrator", "docstatus": 0}]
		with self.assertRaises(frappe.ValidationError):
			validate_document(bad)
		with self.assertRaises(frappe.ValidationError):
			validate_document(dict(document, image="/private/files/item.png"))
		self.assertFalse(frappe.db.exists("Item", self.prefix))

	def test_missing_business_dependency_is_explicit_and_permissions_apply(self):
		doc = frappe.get_doc(
			dict(
				doctype="Item",
				name=self.prefix,
				item_code=self.prefix,
				stock_uom="Nos",
				item_group="All Item Groups",
			)
		)
		doc.append("item_defaults", {"company": "Missing Streaming Company"})
		doc.item_defaults[0].name = self.prefix + "-default"
		with self.assertRaisesRegex(frappe.ValidationError, "Missing catalog dependency"):
			apply_document(frappe._dict(doctype="Master Instance"), snapshot(doc))
		self.assertFalse(frappe.db.exists("Item", self.prefix))
		user = frappe.get_doc(
			dict(
				doctype="User",
				email=frappe.generate_hash(length=8) + "@example.invalid",
				first_name="Streaming",
				send_welcome_email=0,
			)
		).insert()
		doc.item_defaults = []
		frappe.set_user(user.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				apply_document(frappe._dict(doctype="Master Instance"), snapshot(doc))
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Item", self.prefix))

	def test_existing_child_identity_cannot_be_assigned_to_another_item(self):
		item = frappe.get_doc(
			dict(doctype="Item", item_code=self.prefix, stock_uom="Nos", item_group="All Item Groups")
		).insert()
		document = snapshot(item)
		document["name"] = document["item_code"] = self.prefix + " Other"
		with self.assertRaisesRegex(frappe.ValidationError, "child row identity belongs"):
			apply_document(frappe._dict(doctype="Master Instance"), document)
		self.assertFalse(frappe.db.exists("Item", document["name"]))
		self.assertEqual(frappe.get_doc("Item", item.name).uoms[0].uom, "Nos")

	def test_variant_fetches_template_and_attribute_dependencies(self):
		from erpnext.controllers.item_variant import create_variant

		attribute = frappe.get_doc(
			dict(
				doctype="Item Attribute",
				attribute_name=self.prefix + " Size",
				item_attribute_values=[
					{"attribute_value": "Small", "abbr": "S"},
					{"attribute_value": "Large", "abbr": "L"},
				],
			)
		).insert()
		template = frappe.get_doc(
			dict(
				doctype="Item",
				item_code=self.prefix + " Template",
				stock_uom="Nos",
				item_group="All Item Groups",
				has_variants=1,
				attributes=[{"attribute": attribute.name}],
			)
		).insert()
		variant = create_variant(template.name, {attribute.name: "Small"}).insert()
		document = snapshot(variant)
		dependencies = {(doc.doctype, doc.name): snapshot(doc) for doc in (template, attribute)}
		for doc in (variant, template, attribute):
			frappe.delete_doc(doc.doctype, doc.name)

		def fetch(_peer, _method, **args):
			return {"document": dependencies[(args["doctype"], args["name"])]}

		with (
			patch("arab_streaming.streaming.connections.wire_identity", return_value={}),
			patch("arab_streaming.streaming.client.remote_call", side_effect=fetch),
		):
			apply_document(frappe._dict(doctype="Master Instance"), document)
		actual = frappe.get_doc("Item", variant.name)
		self.assertEqual(actual.variant_of, template.name)
		self.assertEqual(actual.attributes[0].attribute_value, "Small")
		self.assertEqual(digest(snapshot(actual)), digest(document))
