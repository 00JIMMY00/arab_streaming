import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe

from arab_streaming.streaming import files


class TestPackagedAssets(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.TemporaryDirectory()
		self.addCleanup(self.directory.cleanup)
		self.root = Path(self.directory.name) / "public"
		self.root.mkdir()
		(self.root / "card.png").write_bytes(b"test-image")
		for name, value in (
			("get_installed_apps", lambda: ["rms"]),
			("get_app_path", lambda *a: str(self.root)),
		):
			p = patch.object(frappe, name, side_effect=value)
			p.start()
			self.addCleanup(p.stop)

		def reject(message):
			raise frappe.ValidationError(message)

		p = patch.object(frappe, "throw", side_effect=reject)
		p.start()
		self.addCleanup(p.stop)

	def test_asset_and_cache_query_are_valid_without_file_records(self):
		for url in ("/assets/rms/card.png", "/assets/rms/card.png?v=3"):
			self.assertTrue(files.packaged_asset(url))

	def test_asset_freeze_does_not_read_file_storage(self):
		document = dict(doctype="Item", name="test", image="/assets/rms/card.png")
		with (
			patch.object(files, "references", return_value=[(document, "image", document["image"])]),
			patch.object(frappe, "db", Mock()) as db,
		):
			self.assertEqual(files.freeze(document), [])
			db.get_value.assert_not_called()

	def test_receiver_keeps_the_asset_without_downloading_it(self):
		document = dict(doctype="Item", name="test", image="/assets/rms/card.png")
		with (
			patch.object(files, "references", return_value=[(document, "image", document["image"])]),
			patch("arab_streaming.streaming.client.remote_call") as remote,
		):
			files.import_files({}, document)
			remote.assert_not_called()
		self.assertEqual(document["image"], "/assets/rms/card.png")

	def test_traversal_unknown_app_and_missing_assets_are_rejected(self):
		for url in (
			"/assets/rms/../private/key",
			"/assets/rms/%2e%2e/private/key",
			"/assets/rms/a\\b",
			"/assets/unknown/card.png",
			"/assets/rms/missing.png",
		):
			with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
				files.packaged_asset(url)

	def test_symlink_outside_public_folder_is_rejected(self):
		secret = self.root.parent / "private.txt"
		secret.write_text("private")
		(self.root / "leak.txt").symlink_to(secret)
		with self.assertRaises(frappe.ValidationError):
			files.packaged_asset("/assets/rms/leak.txt")

	def test_plain_http_and_non_asset_paths_remain_rejected_by_freeze(self):
		for url in ("http://example.invalid/image.png", "/private/key", "file:///etc/passwd"):
			document = dict(doctype="Item", name="test", image=url)
			with (
				patch.object(files, "references", return_value=[(document, "image", url)]),
				self.assertRaises(frappe.ValidationError),
			):
				files.freeze(document)
