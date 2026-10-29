frappe.listview_settings['Dispatch Event'] = {
	add_fields: ['status', 'event_type'],
	get_indicator(doc) {
		const colors = {Pending: 'orange', Delivered: 'blue', Applied: 'green', Failed: 'red',
			'Delivery Failed': 'red', Ignored: 'gray', Skipped: 'gray', 'Completed with Skips': 'gray',
			'Partially Delivered': 'blue', 'Partially Applied': 'orange', 'Partially Ignored': 'orange',
			Synced: 'blue', 'Partially Synced': 'blue'};
		const label = doc.status === 'Synced' ? 'Delivered' : doc.status === 'Partially Synced' ? 'Partially Delivered' : doc.status;
		return [__(label), colors[doc.status] || 'gray', `status,=,${doc.status}`];
	}
};
