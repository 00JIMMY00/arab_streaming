frappe.listview_settings['Streaming Copy Run'] = {
 add_fields:['status'],
 get_indicator(doc) {
  return [__(doc.status), {Queued:'blue',Copying:'blue',Waiting:'orange','Needs Attention':'red',Complete:'green',Failed:'red',Stopped:'gray'}[doc.status] || 'gray', `status,=,${doc.status}`];
 }
};
