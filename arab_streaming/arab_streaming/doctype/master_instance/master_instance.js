frappe.ui.form.on('Master Instance', {
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
		if (frm.__streaming_refresh_timer) clearTimeout(frm.__streaming_refresh_timer);
		if (frm.is_new()) return;
        frm.add_custom_button(__('Reconcile Stock'), () => frappe.call({method:'arab_streaming.api.actions.reconcile_stock', args:{doctype:frm.doctype,name:frm.doc.name}, freeze:true, callback:r=>frappe.msgprint(JSON.stringify(r.message))}), __('Events'));
		const action = (method, args = {}) => {
			if (frm.is_dirty()) return frappe.msgprint(__('Save your changes before running this action.'));
			return frappe.call({
			method: `arab_streaming.api.actions.${method}`, args: {name: frm.doc.name, ...args},
			freeze: true, callback(r) {
				if (method === 'ping') frappe.show_alert({message: __('Ping successful'), indicator: 'green'});
				if (r.message?.queued) frappe.show_alert(__('Queued. Progress and event results update separately.'));
				frm.reload_doc();
			}
			});
		};
		const running = frm.doc.syncing_status === 'In Progress';
		for (const field of ['domain', 'access_key', 'secret', 'site_domain', 'execution_user', 'initial_sync_doctypes', 'syncing_doctypes']) {
			frm.set_df_property(field, 'read_only', running ? 1 : 0);
		}
		if (running) frm.__streaming_refresh_timer = setTimeout(() => {
			const route = frappe.get_route();
			if (route[1] === 'Master Instance' && route[2] === frm.doc.name && !frm.is_dirty()) frm.reload_doc();
		}, 5000);
		if (frm.doc.enabled && frm.doc.syncing_status !== 'In Progress') {
			frm.add_custom_button(__('Link'), () => {
				if (frm.is_dirty()) return frappe.msgprint(__('Save your changes before Link.'));
				if (!frm.doc.execution_user) return frappe.msgprint(__('Choose an Execution User first, then Save.'));
				if (frm.doc.slave_name) return action('link', {branch: frm.doc.restaurant_branch});
				frappe.call({method: 'arab_streaming.api.actions.eligible_branches', args: {name: frm.doc.name},
					callback(r) {
						if (!r.message?.length) return frappe.msgprint(__('No enabled, unassigned branches are available. Enable a Branch on the master first.'));
						frappe.prompt([{fieldname: 'branch', label: __('Branch'), fieldtype: 'Select', options: r.message, reqd: 1}],
							values => action('link', values), __('Link to master'), __('Link'));
					}
				});
			}, __('Connection'));
			if (frm.doc.slave_name) frm.add_custom_button(__('Ping'), () => action('ping'), __('Connection'));
		}
		if (frm.doc.connection_status === 'Connected') {
			if (frm.doc.syncing_status === 'In Progress') {
				frm.add_custom_button(__('Initial Sync Failed'), () => frappe.confirm(
					__('Stop this initial copy? Committed records will remain. You can Continue afterward.'),
					() => action('fail_sync')), __('Previous Master Copy'));
			} else if (frm.doc.syncing_status === 'Failed') {
				if (frm.doc.syncing_status === 'Failed') frm.add_custom_button(__('Restart Copy'), () => action('restart_sync'), __('Previous Master Copy'));
				frm.add_custom_button(__('Count Records'), () => action('count'), __('Previous Master Copy'));
				frm.add_custom_button(__(frm.doc.syncing_status === 'Failed' ? 'Continue' : 'Sync'),
					() => action(frm.doc.syncing_status === 'Failed' ? 'continue_sync' : 'sync'), __('Previous Master Copy'));
			}
			frm.add_custom_button(__('Copy Existing Records'), () => frappe.set_route('List', 'Streaming Rule'));
			frm.add_custom_button(__('Fetch Remote Events'), () => action('fetch'), __('Events'));
			frm.add_custom_button(__('Execute Pending Events'), () => action('execute'), __('Events'));
			frm.add_custom_button(__('Disconnect'), () => action('disconnect'), __('Connection'));
		}
		frm.add_custom_button(__(frm.doc.enabled ? 'Disable' : 'Enable'), () => action('set_enabled', {
			doctype: 'Master Instance', enabled: frm.doc.enabled ? 0 : 1
		}), __('Connection'));
		frm.dashboard.set_headline(frm.doc.connection_status === 'Connected'
			? __('Sync Rules on the master controls both directions. Saved business changes synchronize automatically. For older records, open a Streaming Rule and choose Copy Existing Records.')
			: __('Save the connection details and Execution User, then use Connection → Link.'));
	}
});
