import unittest
from unittest.mock import Mock, patch

import frappe

from arab_streaming.streaming.ownership import assert_local_branch_writer


class TestBranchWriter(unittest.TestCase):
	def check(self, role, branch, paired, blocked):
		with (
			patch.object(
				frappe, "conf", frappe._dict(arab_streaming_site_role=role, arab_streaming_enabled=0)
			),
			patch.object(frappe, "db", Mock()) as db,
			patch("frappe.throw", side_effect=frappe.PermissionError),
		):
			db.exists.return_value = paired
			if blocked:
				with self.assertRaises(frappe.PermissionError):
					assert_local_branch_writer(branch)
			else:
				assert_local_branch_writer(branch)
			return db

	def test_paired_master_is_blocked_even_when_transport_is_paused(self):
		db = self.check("Master", "B1", True, True)
		self.assertEqual(
			db.exists.call_args.args,
			("Slave Instance", {"restaurant_branch": "B1", "connection_status": "Connected"}),
		)

	def test_unpaired_master_branches_remain_local(self):
		self.check("Master", "B2", False, False)
		self.check("Master", None, False, False)

	def test_slave_can_operate_only_its_existing_pair(self):
		self.check("Slave", "B1", True, False)
		self.check("Slave", "B2", False, True)
		self.check("Slave", None, False, True)

	def test_unconfigured_installation_is_unchanged(self):
		db = self.check(None, "B1", False, False)
		db.exists.assert_not_called()
