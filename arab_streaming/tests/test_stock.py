import unittest
from datetime import datetime, timedelta, timezone

import frappe

from arab_streaming.protocol.codec import digest
from arab_streaming.streaming.stock import validate_freshness


class TestStockWatermark(unittest.TestCase):
	def test_snapshot_requires_real_fresh_timestamp_hash_and_integer_sequence(self):
		now = datetime.now(timezone.utc)
		data = {"branch": "A", "movements": []}
		valid = dict(
			captured_at=now.isoformat(),
			source_sequence=str(2**53 + 1),
			data=data,
			payload_digest=digest(data),
		)
		validate_freshness(valid, 900, now)
		for change in [
			dict(captured_at=(now - timedelta(seconds=901)).isoformat()),
			dict(captured_at=(now + timedelta(seconds=61)).isoformat()),
			dict(captured_at=""),
			dict(source_sequence="1.5"),
			dict(source_sequence=str(2**63)),
			dict(payload_digest="wrong"),
		]:
			with self.assertRaises(frappe.ValidationError):
				validate_freshness(dict(valid, **change), 900, now)
