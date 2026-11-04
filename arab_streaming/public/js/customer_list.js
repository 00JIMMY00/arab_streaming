// Keep ERPNext's existing Customer list actions and filters intact.
{
	const previous = frappe.listview_settings.Customer || {};
	const previousOnload = previous.onload;
	frappe.listview_settings.Customer = {
		...previous,
		onload(listview) {
			if (previousOnload) previousOnload.call(this, listview);
			if (frappe.boot.arab_streaming_role === "Master" && frappe.user_roles.includes("System Manager")) {
				listview.page.add_inner_button(__("Bulk Import Customers"), () => frappe.set_route("customer-import-tool"));
			}
		},
	};
}
