frappe.listview_settings['Streaming Rule'] = {
 add_fields: ['doctype_name','enabled','direction','scope','configuration_status'],
 get_indicator(doc) {
  if (!doc.enabled) return [__('Disabled'),'gray','enabled,=,0'];
  if (doc.configuration_status === 'Needs Setup') return [__('Needs Setup'),'orange','configuration_status,=,Needs Setup'];
  return [__(doc.direction),'green','enabled,=,1'];
 }
};
