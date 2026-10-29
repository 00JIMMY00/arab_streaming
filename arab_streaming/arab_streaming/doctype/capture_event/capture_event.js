frappe.ui.form.on('Capture Event', {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.set_df_property('event_sequence', 'hidden', 1);
		const action = async (method, args = {}) => {
			const r = await frappe.call({method: `arab_streaming.api.actions.${method}`,
				args: {name: frm.doc.name, ...args}, freeze: true});
			if (r.message?.queued) frappe.show_alert({message: __('Retry queued. The same event will be processed again.'), indicator: 'blue'});
			await frm.reload_doc();
		};
		if (frm.doc.status === 'Failed') {
			frm.dashboard.add_comment(__('Application failed on this site. Fix the error below, then click Retry Now. Automatic retries also continue with backoff; retrying does not change the captured data.'), 'red', true);
			if (frm.doc.exception) frm.dashboard.add_comment(frappe.utils.escape_html(frm.doc.exception), 'red', true);
			frm.page.set_primary_action(__('Retry Now'), () => action('retry_capture'));
			frm.add_custom_button(__('Retry Now'), () => action('retry_capture'));
			frm.add_custom_button(__('Ignore'), () => frappe.prompt([
				{fieldname: 'reason', label: __('Reason'), fieldtype: 'Small Text', reqd: 1}
			], values => action('ignore_capture', values), __('Ignore this event')));
		} else if (frm.doc.status === 'Pending') {
			frm.dashboard.add_comment(__('Waiting for application. Refresh to see the result. Earlier events for the same record must finish first.'), 'blue', true);
		}
		frm.add_custom_button(__('Refresh Status'), () => frm.reload_doc());
		frm.add_custom_button(__('Execute Pending Events'), () => action('execute'));
		if (frm.doc.instance_type === 'Master Instance') frm.add_custom_button(__('Fetch Remote Events'),
			() => frappe.call({method: 'arab_streaming.api.actions.fetch', args: {name: frm.doc.instance_name},
				callback: () => frappe.show_alert(__('Fetch queued'))}));
		if (frm.doc.event_type !== 'Delete') frm.add_custom_button(__('View Reference'),
			() => frappe.set_route('Form', frm.doc.reference_doctype, frm.doc.reference_name));
	}
});
