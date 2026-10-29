import frappe


def wakeup():
	if not frappe.conf.get("arab_streaming_enabled"):
		return
	try:
		for item in frappe.get_all(
			"Streaming Initial Sync Run",
			filters={"status": "In Progress"},
			fields=["name", "master_instance", "lease_token"],
			limit_page_length=10,
		):
			frappe.enqueue(
				"arab_streaming.streaming.initial_sync.run_initial",
				name=item.master_instance,
				run_name=item.name,
				token=item.lease_token,
				timeout=900,
				job_id="streaming-initial-" + item.name + "-" + item.lease_token,
				deduplicate=True,
			)
		frappe.enqueue(
			"arab_streaming.tasks.run",
			queue="default",
			timeout=180,
			job_id="arab-streaming-events",
			deduplicate=True,
		)
	except Exception:
		# The minute scanner recovers committed database work after Redis/enqueue failure.
		pass


def run():
	if not frappe.conf.get("arab_streaming_enabled") or frappe.conf.get("arab_streaming_site_role") not in (
		"Master",
		"Slave",
	):
		return
	from arab_streaming.streaming.delivery import run_peer

	dt = "Slave Instance" if frappe.conf.arab_streaming_site_role == "Master" else "Master Instance"
	for name in frappe.get_all(dt, filters={"enabled": 1, "connection_status": "Connected"}, pluck="name"):
		run_peer(dt, name)


def execute_only(only_peer=None):
	if not frappe.conf.get("arab_streaming_enabled"):
		return
	from arab_streaming.streaming.executor import execute_captures

	execute_captures(only_peer=only_peer)


def fetch_only(name):
	if not frappe.conf.get("arab_streaming_enabled"):
		return
	from arab_streaming.streaming.transport import exchange

	exchange(frappe.get_doc("Master Instance", name))
