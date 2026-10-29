frappe.provide('arab_streaming');
arab_streaming.copy_existing_dialog = async frm => {
 if (frm.is_dirty()) return frappe.msgprint(__('Save the rule before copying records.'));
 const opts = (await frappe.call({method:'arab_streaming.api.copy.options',args:{name:frm.doc.name}})).message;
 if (!opts.directions.length || !opts.branches.length) return frappe.msgprint(__('This rule needs an allowed direction and an enabled, connected Branch.'));
 const esc = frappe.utils.escape_html;
 let preview, requestId, runName, timer, d;
 const values = () => d.get_values();
 const invoke = async (method, extra={}) => (await frappe.call({method:`arab_streaming.api.copy.${method}`,
  args:{name:frm.doc.name,direction:values().direction,branch:values().branch,...extra},freeze:method !== 'progress'})).message;
 const show = run => {
  const runUrl = `${run.source_url.replace(/\/$/,'')}/app/streaming-copy-run/${encodeURIComponent(run.name)}`;
  d.fields_dict.preview.$wrapper.html(`<p><strong>${esc(__(run.status))}</strong> — <a href="${esc(runUrl)}" target="_blank" rel="noopener noreferrer">${__('Open Copy Run')}</a></p>
   <p>${__('Queued')}: ${run.queued_records}; ${__('Applied')}: ${run.applied_records}; ${__('Preserved / Skipped')}: ${run.skipped_records}; ${__('Failed')}: ${run.failed_records}; ${__('Waiting')}: ${run.pending_records}</p>
   ${run.exception ? `<div class="alert alert-danger">${esc(run.exception)}</div>` : ''}
   <p class="text-muted">${__('Failures stay visible on their existing events. Resume continues a failed source scan; it does not bypass receiver validation.')}</p>`);
  d.set_primary_action(__('Refresh Status'), refresh);
  if (run.status === 'Failed') d.set_primary_action(__('Resume Copy'), async () => show(await invoke('control',{run_name:runName,action:'resume'})));
  clearTimeout(timer);
  if (['Queued','Copying','Waiting','Needs Attention'].includes(run.status)) timer=setTimeout(refresh,5000);
 };
 const refresh = async () => {
  if (!runName || !d.$wrapper.is(':visible')) return;
  try { show(await invoke('progress',{run_name:runName})); } catch (_) { clearTimeout(timer); }
 };
 const reset = () => {
  if (!d || runName) return;
  preview=null; requestId=null;
  d.fields_dict.preview.$wrapper.empty();
  d.set_primary_action(__('Preview Copy'), doPreview);
 };
 const doStart = async () => {
  if (!preview) return doPreview();
  requestId ||= crypto.randomUUID();
  const run = await invoke('start',{request_id:requestId,existing_policy:values().existing_policy,revision:preview.rule_revision});
  runName=run.name;
  for (const field of ['direction','branch','existing_policy']) d.set_df_property(field,'read_only',1);
  show(run);
 };
 const doPreview = async () => {
  if (!values()) return;
  preview=await invoke('preview');
  const history=(preview.recent_runs || []).map(run => `<li><a href="${esc(preview.source_url.replace(/\/$/,'')+'/app/streaming-copy-run/'+encodeURIComponent(run.name))}" target="_blank" rel="noopener noreferrer">${esc(run.name)}</a>: ${esc(__(run.status))}</li>`).join('');
  d.fields_dict.preview.$wrapper.html(`<div class="alert alert-info"><strong>${esc(preview.source_url)}</strong> → <strong>${esc(preview.destination_url)}</strong><br>
   ${__('Matching source records')}: <strong>${preview.at_least ? __('At least')+' ' : ''}${preview.count}</strong></div>
   ${!preview.count && !preview.at_least ? `<div class="alert alert-warning">${__('No source records match. Check the source record’s Branch and the rule’s filters or Branch Field.')}</div>` : ''}
   <p>${__('This run copies records that existed when it starts, using the saved Branch and rule filters. Records are read in small batches; new changes continue through normal sync.')}</p>
   <p>${values().existing_policy === 'Skip' ? __('Existing destination records will be preserved.') : __('Existing destination records follow the rule policy: {0}.',[esc(opts.duplicate_policy)])}</p>
   ${history ? `<p>${__('Recent copy runs for this Branch and direction')}:</p><ul>${history}</ul>` : ''}`);
  d.set_primary_action(__(preview.count || preview.at_least ? 'Start Copy' : 'Preview Copy'), preview.count || preview.at_least ? doStart : doPreview);
 };
 d = new frappe.ui.Dialog({title:__('Copy Existing Records'),fields:[
  {fieldname:'direction',label:__('Direction'),fieldtype:'Select',options:opts.directions,default:opts.directions[0],reqd:1,onchange:reset},
  {fieldname:'branch',label:__('Branch'),fieldtype:'Select',options:opts.branches,default:opts.branches[0],reqd:1,onchange:reset},
  {fieldname:'existing_policy',label:__('Existing Destination Records'),fieldtype:'Select',options:[
   {label:__('Preserve existing records'),value:'Skip'},
   {label:__('Follow rule ({0})',[opts.duplicate_policy]),value:'Rule'}],default:'Skip',reqd:1,onchange:reset},
  {fieldname:'preview',fieldtype:'HTML'}
 ],primary_action_label:__('Preview Copy'),primary_action:doPreview,onhide:()=>clearTimeout(timer)});
 d.show();
};

frappe.ui.form.on('Streaming Rule', {
 setup(frm) { frm.set_query('doctype_name', () => ({query: "arab_streaming.api.rules.doctype_query"})); },
 refresh(frm) {
  if (!frm.is_new()) frm.add_custom_button(__('Copy Existing Records'), () => arab_streaming.copy_existing_dialog(frm));
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
