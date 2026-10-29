"""Supervised low-latency peer agents: python -m arab_streaming.agent --site SITE."""

import argparse
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import frappe

stop = threading.Event()


def one_peer(site, sites_path, instance_type, name, interval):
	from arab_streaming.streaming.delivery import run_peer

	while not stop.is_set():
		started = time.monotonic()
		try:
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.set_user(
				"Administrator"
			)  # service orchestration only; executor always switches to the approved integration user
			if not frappe.conf.get("arab_streaming_enabled"):
				return
			run_peer(instance_type, name)
		except Exception:
			# Durable peer errors are written by run_peer. A database outage is retried.
			pass
		finally:
			frappe.destroy()
		stop.wait(max(0.05, interval - (time.monotonic() - started)))


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--site", required=True)
	parser.add_argument("--sites-path", default=".")
	parser.add_argument("--interval", type=float, default=1.0)
	parser.add_argument("--peers", type=int, default=32)
	args = parser.parse_args()
	if args.interval < 0.2 or not 1 <= args.peers <= 128:
		parser.error("Interval must be >= 0.2 seconds and peers 1..128")
	for sig in (signal.SIGINT, signal.SIGTERM):
		signal.signal(sig, lambda *_: stop.set())
	futures = {}
	with ThreadPoolExecutor(max_workers=args.peers) as pool:
		while not stop.is_set():
			try:
				frappe.init(site=args.site, sites_path=args.sites_path)
				frappe.connect()
				role = frappe.conf.get("arab_streaming_site_role")
				dt = "Slave Instance" if role == "Master" else "Master Instance"
				if frappe.conf.get("arab_streaming_enabled") and role in ("Master", "Slave"):
					for name in frappe.get_all(
						dt, filters={"enabled": 1, "connection_status": "Connected"}, pluck="name"
					):
						if name not in futures or futures[name].done():
							futures[name] = pool.submit(
								one_peer, args.site, args.sites_path, dt, name, args.interval
							)
			finally:
				frappe.destroy()
			stop.wait(5)


if __name__ == "__main__":
	main()
