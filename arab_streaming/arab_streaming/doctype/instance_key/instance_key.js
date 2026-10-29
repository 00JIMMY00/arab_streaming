frappe.ui.form.on("Instance Key", {
	refresh(frm) {
		if (frm.is_new() || frappe.session.user !== "Administrator") {
			return;
		}

		frm.add_custom_button(__("Show API Secret"), async () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the Instance Key before showing its credentials."));
				return;
			}

			const response = await frm.call("reveal_secret");
			if (!response.message) {
				return;
			}

			const dialog = new frappe.ui.Dialog({
				title: __("Instance Credentials"),
				fields: [
					{
						fieldname: "access_key",
						fieldtype: "Data",
						label: __("Access Key"),
						read_only: 1,
						default: frm.doc.api_key || frm.doc.name,
					},
					{
						fieldname: "secret",
						fieldtype: "Data",
						label: __("API Secret"),
						read_only: 1,
						default: response.message,
					},
				],
				primary_action_label: __("Close"),
				primary_action() {
					dialog.hide();
				},
				onhide() {
					dialog.set_value("secret", "");
					dialog.fields_dict.secret.df.default = "";
					dialog.$wrapper.remove();
				},
			});
			// Keep plaintext out of the saved document and form model.
			response.message = null;
			dialog.show();
		});
	},
});
