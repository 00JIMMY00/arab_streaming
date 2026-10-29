frappe.ui.form.on('Streaming Copy Run', {
 refresh(frm) {
  if (frm.is_new()) return;
  if (frm.__copy_timer) clearTimeout(frm.__copy_timer);
  frm.dashboard.set_headline(__(frm.doc.status === 'Complete'
   ? 'Copy complete. Applied and preserved records are counted separately.'
   : frm.doc.status === 'Needs Attention'
   ? 'Some records failed on the receiver. Open Copy Events, fix the error and retry the existing event.'
   : 'Records are copied in small batches. Queued records still need successful application on the receiver.'));
  const events = failed => {
   frappe.route_options = {copy_run:frm.doc.name};
   if (failed) frappe.route_options.status = 'Failed';
   frappe.set_route('List', 'Dispatch Event');
  };
  frm.add_custom_button(__('Copy Events'), () => events(false));
  if (frm.doc.failed_records) frm.add_custom_button(__('Failed Events'), () => events(true));
  frm.add_custom_button(__('Refresh Status'), async () => { await frm.reload_doc(); });
  const control = async action => {
   await frappe.call({method:'arab_streaming.api.copy.control_run', args:{name:frm.doc.name,action},freeze:true});
   await frm.reload_doc();
  };
  if (frm.doc.status === 'Failed') frm.page.set_primary_action(__('Resume Copy'), () => control('resume'));
  if (['Queued','Copying','Waiting','Needs Attention','Failed'].includes(frm.doc.status)) {
   frm.add_custom_button(__('Stop Copy'), () => frappe.confirm(
    __('Stop adding records to this copy? Events already queued will continue processing.'), () => control('stop')));
   frm.__copy_timer = setTimeout(() => {
    const route = frappe.get_route();
    if (route[1] === frm.doctype && route[2] === frm.doc.name) frm.reload_doc();
   }, 5000);
  }
 }
});
