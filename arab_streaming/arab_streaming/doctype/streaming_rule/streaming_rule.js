frappe.ui.form.on('Streaming Rule', {
 setup(frm) { frm.set_query('doctype_name', () => ({query: "arab_streaming.api.rules.doctype_query"})); },
 refresh(frm) {
        if (!frm.is_new() && frappe.boot.arab_streaming_role === "Master") frm.add_custom_button(__("Copy Existing Records"), () => frappe.call({method: "arab_streaming.api.rules.copy_existing", args: {name: frm.doc.name}, freeze:true, callback(r) {frappe.msgprint({title: __("Initial copy"), message: `<pre>${frappe.utils.escape_html(JSON.stringify(r.message,null,2))}</pre>`});}}));
  if (frappe.boot.arab_streaming_role === 'Slave') {
   frm.disable_save(); frm.set_intro(__('Published by the master. Manage direction and participating branches in Streaming Rules on the master.'));
   return;
  }
  frm.add_custom_button(__('Set Up Branch Field'), () => {
   if (!frm.doc.doctype_name) return frappe.msgprint(__('Choose a DocType first.'));
   frappe.call({method:'arab_streaming.api.rules.setup_branch',args:{doctype:frm.doc.doctype_name},freeze:true,callback:r=>frm.set_value('branch_field',r.message.branch_field)});
  });
  if (!frm.is_new()) frm.add_custom_button(__('Test Configuration'), () => {
   if (frm.is_dirty()) return frappe.msgprint(__('Save this rule first.'));
   frappe.call({method:'arab_streaming.api.rules.test_configuration',args:{name:frm.doc.name},freeze:true,callback:r=>{frappe.msgprint({title:__('Configuration Check'),message:(r.message.branches || []).map(x=>`${frappe.utils.escape_html(x.branch)}: ${frappe.utils.escape_html(x.status)} ${frappe.utils.escape_html(x.error || '')}`).join('<br>')});frm.reload_doc();}});
  });
  frm.set_intro(__('Choose DocType, direction and branches once. Saving business documents queues delivery automatically. Branch scope prevents data crossing branches.'));
 },
 doctype_name(frm) {
  if (!frm.doc.doctype_name) return;
  frappe.call({method:'arab_streaming.api.rules.describe',args:{doctype:frm.doc.doctype_name},callback:r=>{frm.set_value('scope',r.message.scope);frm.set_value('branch_field',r.message.branch_field || '');}});
 }
});
