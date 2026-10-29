"""Named fixtures for isolated test databases only."""

import frappe
from frappe.utils import getdate, today

COMPANY = "_Arab Streaming Acceptance"
ABBR = "AST"
CUSTOMER = "_Arab Streaming Acceptance Customer"
ITEM = "_ARAB-STREAMING-ACCEPTANCE-ITEM"
USER = "streaming.acceptance@example.invalid"


def setup():
	if not frappe.local.site.endswith(".test"):
		raise RuntimeError("Acceptance fixtures must run on isolated .test sites")
	frappe.set_user("Administrator")
	frappe.conf.arab_streaming_enabled = False
	from arab_streaming.streaming.policy import ensure_dimension

	ensure_dimension()
	if not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc(dict(doctype="Warehouse Type", name="Transit")).insert()
	if not frappe.db.exists("Price List", "Standard Selling"):
		frappe.get_doc(
			dict(
				doctype="Price List",
				price_list_name="Standard Selling",
				selling=1,
				buying=0,
				currency="USD",
				enabled=1,
			)
		).insert()
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc(
			dict(
				doctype="Company",
				company_name=COMPANY,
				abbr=ABBR,
				default_currency="USD",
				country="United States",
				chart_of_accounts="Standard",
			)
		).insert()
	for dt, key, name in [
		("UOM", "uom_name", "Nos"),
		("Item Group", "item_group_name", "All Item Groups"),
		("Customer Group", "customer_group_name", "All Customer Groups"),
		("Territory", "territory_name", "All Territories"),
	]:
		if not frappe.db.exists(dt, name):
			frappe.get_doc(
				dict(doctype=dt, **{key: name}, **({"is_group": 1} if dt != "UOM" else {}))
			).insert()
	year = getdate(today()).year
	label = f"_AST {year}"
	if not frappe.db.exists(
		"Fiscal Year", {"year_start_date": ["<=", today()], "year_end_date": [">=", today()]}
	):
		frappe.get_doc(
			dict(
				doctype="Fiscal Year",
				year=label,
				year_start_date=f"{year}-01-01",
				year_end_date=f"{year}-12-31",
			)
		).insert()
	if not frappe.db.exists("Customer Group", "_AST Customers"):
		frappe.get_doc(
			dict(
				doctype="Customer Group",
				customer_group_name="_AST Customers",
				parent_customer_group="All Customer Groups",
				is_group=0,
			)
		).insert()
	if not frappe.db.exists("Item Group", "_AST Items"):
		frappe.get_doc(
			dict(
				doctype="Item Group",
				item_group_name="_AST Items",
				parent_item_group="All Item Groups",
				is_group=0,
			)
		).insert()
	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			dict(
				doctype="Customer",
				customer_name=CUSTOMER,
				customer_type="Company",
				customer_group="_AST Customers",
				territory="All Territories",
			)
		).insert()
	if not frappe.db.exists("Item", ITEM):
		frappe.get_doc(
			dict(
				doctype="Item",
				item_code=ITEM,
				item_name="Streaming acceptance stock item",
				item_group="_AST Items",
				stock_uom="Nos",
				is_stock_item=1,
				item_defaults=[
					dict(
						company=COMPANY,
						default_warehouse=frappe.db.get_value(
							"Warehouse", {"company": COMPANY, "is_group": 0}, "name"
						),
						expense_account=frappe.db.get_value(
							"Account", {"company": COMPANY, "root_type": "Expense", "is_group": 0}, "name"
						),
						income_account=frappe.db.get_value(
							"Account", {"company": COMPANY, "root_type": "Income", "is_group": 0}, "name"
						),
						buying_cost_center=frappe.db.get_value(
							"Cost Center", {"company": COMPANY, "is_group": 0}, "name"
						),
						selling_cost_center=frappe.db.get_value(
							"Cost Center", {"company": COMPANY, "is_group": 0}, "name"
						),
					)
				],
			)
		).insert()
	if not frappe.db.exists("User", USER):
		frappe.get_doc(
			dict(
				doctype="User",
				email=USER,
				first_name="Streaming Acceptance",
				enabled=1,
				send_welcome_email=0,
				user_type="System User",
			)
		).insert()
	for n in range(10):
		branch = f"_AST Branch {n + 1:02d}"
		if not frappe.db.exists("Branch", branch):
			frappe.get_doc(dict(doctype="Branch", branch=branch, custom_streaming_enabled=1)).insert()
	tax = "_AST Tax - AST"
	if not frappe.db.exists("Account", tax):
		parent = frappe.db.get_value(
			"Account", {"company": COMPANY, "is_group": 1, "account_type": "Tax"}, "name"
		) or frappe.db.get_value(
			"Account", {"company": COMPANY, "is_group": 1, "root_type": "Liability"}, "name"
		)
		frappe.get_doc(
			dict(
				doctype="Account",
				account_name="_AST Tax",
				parent_account=parent,
				company=COMPANY,
				account_type="Tax",
				is_group=0,
			)
		).insert()
	from arab_streaming.streaming.permissions import provision
	from arab_streaming.streaming.policy import stored_rules

	provision(
		USER,
		[dict(r) for r in stored_rules() if r.enabled]
		+ [dict(doctype_name="Sales Invoice", enabled=1), dict(doctype_name="Sales Order", enabled=1)],
	)
	frappe.db.commit()
	return dict(
		company=COMPANY,
		customer=CUSTOMER,
		item=ITEM,
		user=USER,
		tax=tax,
		receivable=frappe.db.get_value(
			"Account", {"company": COMPANY, "account_type": "Receivable", "is_group": 0}, "name"
		),
		income=frappe.db.get_value(
			"Account", {"company": COMPANY, "root_type": "Income", "is_group": 0}, "name"
		),
		cost_center=frappe.db.get_value("Cost Center", {"company": COMPANY, "is_group": 0}, "name"),
		warehouse=frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0}, "name"),
	)


def invoice(f, branch="_AST Branch 01", stock=False):
	return frappe.get_doc(
		dict(
			doctype="Sales Invoice",
			company=f["company"],
			customer=f["customer"],
			debit_to=f["receivable"],
			posting_date=today(),
			due_date=today(),
			currency="USD",
			selling_price_list="Standard Selling",
			price_list_currency="USD",
			plc_conversion_rate=1,
			conversion_rate=1,
			branch=branch,
			update_stock=int(stock),
			items=[
				dict(
					item_code=f["item"],
					qty=2,
					rate=10,
					income_account=f["income"],
					cost_center=f["cost_center"],
					warehouse=f["warehouse"],
				)
			],
			taxes=[
				dict(
					charge_type="On Net Total",
					account_head=f["tax"],
					description="Streaming acceptance tax",
					rate=5,
					cost_center=f["cost_center"],
				)
			],
		)
	).insert()
