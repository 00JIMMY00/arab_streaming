import frappe


def extend_bootinfo(bootinfo):
	bootinfo.arab_streaming_role = frappe.conf.get("arab_streaming_site_role")
	from arab_streaming.streaming.targets import descriptor

	bootinfo.arab_streaming_connection_target = descriptor()
