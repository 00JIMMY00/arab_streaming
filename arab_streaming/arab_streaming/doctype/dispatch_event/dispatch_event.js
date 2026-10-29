frappe.ui.form.on('Dispatch Event', {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.set_df_property('event_sequence', 'hidden', 1);
		const escape = frappe.utils.escape_html;
		const actions = async (method, args = {}) => {
			const r = await frappe.call({
				method: `arab_streaming.api.actions.${method}`, args: {name: frm.doc.name, ...args}, freeze: true
			});
			if (r.message?.queued) frappe.show_alert({
				message: __('Retry queued on receiver. Refresh its status to see the result.'), indicator: 'blue'
			});
			await frm.reload_doc();
		};
		frm.dashboard.add_comment(
			__('Delivered means the receiver stored this event. Applied means it successfully processed it. Failed means the receiver rejected application. Retry after fixing the reported cause.'),
			frm.doc.status === 'Failed' ? 'red' : 'blue', true);
		for (const row of frm.doc.instances || []) {
			const status = row.ignore ? __('Ignored locally') : !row.synced ? __('Not delivered') :
				row.receiver_status === 'Unavailable' ? __('Delivered; receiver result unavailable') :
				row.receiver_status ? __(row.receiver_status) : __('Delivered; awaiting receiver status');
			const link = frm.doc.__onload?.receiver_urls?.[row.name];
			const capture = link ? `<a href="${escape(link)}" target="_blank" rel="noopener noreferrer">${__('Open Receiver Event')}</a>` : '';
			const checked = row.status_checked_at ? __('Last checked: {0}', [escape(frappe.datetime.str_to_user(row.status_checked_at))]) : '';
			frm.dashboard.add_comment(
				`<strong>${escape(row.instance_name)}: ${escape(status)}</strong> ${capture}<br>${checked}` +
				(row.receiver_exception ? `<br>${escape(row.receiver_exception)}` : '') +
				(row.exception ? `<br>${__('Delivery error')}: ${escape(row.exception)}` : ''),
				row.receiver_status === 'Failed' || row.exception ? 'red' : 'blue', true);
		}
		frm.add_custom_button(__('Refresh Receiver Status'), () => actions('refresh_dispatch'));
		const failed = (frm.doc.instances || []).filter(row => row.synced && !row.ignore && row.receiver_status === 'Failed');
		if (failed.length) {
			const retry = async () => {
				if (failed.length === 1) return actions('retry_dispatch', {destination: failed[0].name});
				frappe.prompt([{fieldname: 'destination', label: __('Destination'), fieldtype: 'Select',
					options: failed.map(row => ({label: row.instance_name, value: row.name})), reqd: 1}],
					values => actions('retry_dispatch', values), __('Retry on Receiver'));
			};
			frm.add_custom_button(__('Retry on Receiver'), retry);
			frm.page.set_primary_action(__('Retry on Receiver'), retry);
		}
		frm.add_custom_button(__('Ignore Destination'), () => {
			const rows = frm.doc.instances.filter(row => !row.synced && !row.ignore);
			if (!rows.length) return frappe.msgprint(__('No outstanding destinations.'));
			frappe.prompt([
				{fieldname: 'destination', label: __('Destination'), fieldtype: 'Select', options: rows.map(row => ({label: row.instance_name, value: row.name})), reqd: 1},
				{fieldname: 'reason', label: __('Reason'), fieldtype: 'Small Text', reqd: 1}
			], values => actions('ignore_destination', values));
		});
		if (frm.doc.event_type !== 'Delete') frm.add_custom_button(__('View Reference'),
			() => frappe.set_route('Form', frm.doc.reference_doctype, frm.doc.reference_name));
	}
});
