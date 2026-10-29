frappe.ui.form.on('Slave Instance', {
	refresh(frm) {
		frm.add_custom_button(__('Sync Rules'), () => frappe.set_route('List', 'Streaming Rule'));
        if (!frm.is_new()) frappe.call({method: 'arab_streaming.api.rules.connection_summary', args: {doctype: frm.doctype, name: frm.doc.name}, callback(r) {
            const data = r.message;
            if (!data || !frm.fields_dict.streaming_rules_summary) return;
            const esc = frappe.utils.escape_html;
            frm.fields_dict.streaming_rules_summary.$wrapper.html(`<p><a href='/app/streaming-rule'>${__('Open Streaming Rules')}</a> — ${__('Configure DocType, Direction and Branch on the master.')}</p><table class='table table-bordered'><tr><th>DocType</th><th>Direction</th><th>Scope</th><th>Status</th></tr>${data.rules.map(x => `<tr><td><a href='/app/streaming-rule/${encodeURIComponent(x.name)}'>${esc(x.doctype_name)}</a></td><td>${esc(x.direction)}</td><td>${esc(x.scope)}</td><td>${x.enabled ? 'Enabled' : 'Disabled'}</td></tr>`).join('')}</table><p>${__('Outstanding deliveries')}: ${data.outstanding}; ${__('Pending applications')}: ${data.pending}. ${esc(data.error || '')}</p>`);
        }});
		for (const table of ['syncing_doctypes', 'initial_sync_doctypes']) {
			if (frm.fields_dict[table]) frm.set_query('doctype_name', table, () => ({query: "arab_streaming.api.rules.doctype_query"}));
		}
		if (frm.is_new()) return;
        frm.add_custom_button(__('Reconcile Stock'), () => frappe.call({method:'arab_streaming.api.actions.reconcile_stock', args:{doctype:frm.doctype,name:frm.doc.name}, freeze:true, callback:r=>frappe.msgprint(JSON.stringify(r.message))}), __('Events'));
		frm.dashboard.set_headline(__('Choose DocType, direction and Branch in Sync Rules. Saved business changes synchronize automatically.'));
		if (frappe.session.user !== 'Administrator') return;
		frm.add_custom_button(__(frm.doc.enabled ? 'Disable' : 'Enable'), () => frappe.call({
			method: 'arab_streaming.api.actions.set_enabled',
			args: {doctype: 'Slave Instance', name: frm.doc.name, enabled: frm.doc.enabled ? 0 : 1},
			callback: () => frm.reload_doc()
		}));
	}
});
