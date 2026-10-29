import frappe


def extend_bootinfo(bootinfo):
	bootinfo.arab_streaming_role = frappe.conf.get("arab_streaming_site_role")
