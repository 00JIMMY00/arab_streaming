import copy

import frappe
from frappe.tests.utils import FrappeTestCase

from arab_streaming.protocol.codec import digest, snapshot
from arab_streaming.streaming.context import replay
from arab_streaming.streaming.executor import apply_document
from arab_streaming.tests.finance_helpers import invoice, setup


class TestFinancialLifecycle(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.fixtures = setup()

	def setUp(self):
		self.previous = frappe.session.user
		self.enabled = frappe.conf.get("arab_streaming_enabled")
		frappe.set_user("Administrator")
		frappe.conf.arab_streaming_enabled = False
		frappe.db.savepoint("financial_test")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="financial_test")
		frappe.conf.arab_streaming_enabled = self.enabled
		frappe.set_user(self.previous)

	def ledger(self, name):
		return sorted(
			(r.account, round(r.debit, 4), round(r.credit, 4), r.is_cancelled, r.branch)
			for r in frappe.get_all(
				"GL Entry",
				filters={"voucher_type": "Sales Invoice", "voucher_no": name},
				fields=["account", "debit", "credit", "is_cancelled", "branch"],
			)
		)

	def test_invoice_draft_submit_duplicate_and_cancel_preserve_financial_effects(self):
		frappe.db.savepoint("source_trial")
		source = invoice(self.fixtures)
		draft = snapshot(source)
		source.submit()
		submitted = snapshot(source)
		expected = self.ledger(source.name)
		self.assertEqual(source.grand_total, 21)
		self.assertTrue(expected)
		source.cancel()
		cancelled = snapshot(source)
		expected_cancel = self.ledger(source.name)
		frappe.db.rollback(save_point="source_trial")
		peer = frappe._dict(doctype="Master Instance")
		with replay("financial-test", self.fixtures["user"]):
			apply_document(peer, draft)
			actual = snapshot(frappe.get_doc("Sales Invoice", draft["name"]))
			self.assertEqual({k: (draft[k], actual[k]) for k in draft if draft[k] != actual[k]}, {})
			apply_document(peer, submitted, base_digest=digest(draft))
			self.assertEqual(self.ledger(source.name), expected)
			apply_document(peer, submitted)
			self.assertEqual(self.ledger(source.name), expected)
			apply_document(peer, cancelled)
			self.assertEqual(self.ledger(source.name), expected_cancel)
			self.assertEqual(frappe.get_doc("Sales Invoice", source.name).docstatus, 2)

	def test_submitted_invoice_can_be_copied_without_a_prior_draft(self):
		frappe.db.savepoint("source_trial")
		source = invoice(self.fixtures)
		source.submit()
		document = snapshot(source)
		expected = self.ledger(source.name)
		frappe.db.rollback(save_point="source_trial")
		with replay("financial-test", self.fixtures["user"]):
			apply_document(frappe._dict(doctype="Master Instance"), document)
		self.assertEqual(frappe.get_doc("Sales Invoice", source.name).grand_total, 21)
		self.assertEqual(self.ledger(source.name), expected)

	def test_stock_invoice_submit_and_cancel_have_correct_stock_effects(self):
		from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

		make_stock_entry(
			item_code=self.fixtures["item"],
			target=self.fixtures["warehouse"],
			qty=10,
			rate=5,
			company=self.fixtures["company"],
		)
		frappe.db.savepoint("source_trial")
		source = invoice(self.fixtures, stock=True)
		source.submit()
		document = snapshot(source)
		fields = ["actual_qty", "stock_value_difference", "is_cancelled", "branch"]
		expected = [
			dict(r)
			for r in frappe.get_all(
				"Stock Ledger Entry",
				filters={"voucher_type": "Sales Invoice", "voucher_no": source.name},
				fields=fields,
			)
		]
		source.cancel()
		cancelled = snapshot(source)
		expected_cancel = [
			dict(r)
			for r in frappe.get_all(
				"Stock Ledger Entry",
				filters={"voucher_type": "Sales Invoice", "voucher_no": source.name},
				fields=fields,
			)
		]
		frappe.db.rollback(save_point="source_trial")
		with replay("financial-test", self.fixtures["user"]):
			apply_document(frappe._dict(doctype="Master Instance"), document)
			actual = [
				dict(r)
				for r in frappe.get_all(
					"Stock Ledger Entry",
					filters={"voucher_type": "Sales Invoice", "voucher_no": source.name},
					fields=fields,
				)
			]
			self.assertEqual(actual, expected)
			apply_document(frappe._dict(doctype="Master Instance"), cancelled)
			actual = [
				dict(r)
				for r in frappe.get_all(
					"Stock Ledger Entry",
					filters={"voucher_type": "Sales Invoice", "voucher_no": source.name},
					fields=fields,
				)
			]
			self.assertEqual(actual, expected_cancel)

	def test_other_submittable_doctype_uses_the_same_executor(self):
		from frappe.utils import add_days, today

		frappe.db.savepoint("source_trial")
		source = frappe.get_doc(
			dict(
				doctype="Sales Order",
				company=self.fixtures["company"],
				customer=self.fixtures["customer"],
				transaction_date=today(),
				delivery_date=add_days(today(), 2),
				currency="USD",
				conversion_rate=1,
				branch="_AST Branch 01",
				items=[
					dict(
						item_code=self.fixtures["item"],
						qty=2,
						rate=10,
						delivery_date=add_days(today(), 2),
						warehouse=self.fixtures["warehouse"],
					)
				],
			)
		).insert()
		source.submit()
		document = snapshot(source)
		frappe.db.rollback(save_point="source_trial")
		with replay("financial-test", self.fixtures["user"]):
			apply_document(frappe._dict(doctype="Master Instance"), document)
		actual = frappe.get_doc("Sales Order", source.name)
		self.assertEqual(actual.docstatus, 1)
		self.assertEqual(actual.grand_total, 20)
