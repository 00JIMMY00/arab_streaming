frappe.listview_settings['Capture Event'] = {
	add_fields: ['status', 'event_type', 'instance_name', 'remote_dispatch_event'],
	get_indicator(doc) {
		return [__(doc.status), {Pending: 'orange', Success: 'green', Skipped: 'gray', Failed: 'red', Ignored: 'gray'}[doc.status], `status,=,${doc.status}`];
	},
	onload(listview) {
		listview.page.add_inner_button(__('Execute Pending Events'), () => frappe.call({
			method: 'arab_streaming.api.actions.execute', callback: () => frappe.show_alert(__('Execution queued'))
		}));
	}
};
