import copy
import json
import unittest

from arab_streaming.protocol.envelope import ProtocolError, decode_batch, payload_digest, validate_envelope
from arab_streaming.protocol.version import PROTOCOL, VERSION


def example():
	envelope = dict(
		protocol=PROTOCOL,
		version=VERSION,
		source_installation_id="source-test",
		pairing_id="pair-test",
		pairing_generation="1",
		event_id="event-test-1",
		event_sequence=str(2**53 + 123),
		created_on="2026-10-27T10:00:00+00:00",
		created_by="integration@example.invalid",
		reference_doctype="Item",
		reference_name="صنف تجريبي",
		event_type="Create",
		document_event="after_insert",
		data={"description": "وصف عربي", "rate": "12.500", "optional": None},
		metadata={},
		doctype_config={},
	)
	envelope["payload_digest"] = payload_digest(envelope)
	return envelope


class TestProtocol(unittest.TestCase):
	def test_unicode_and_large_sequence_roundtrip(self):
		envelope = example()
		result = decode_batch(json.dumps([envelope], ensure_ascii=False))
		self.assertEqual(result, [envelope])
		self.assertEqual(result[0]["event_sequence"], "9007199254741115")

	def test_digest_covers_configuration_and_source_identity(self):
		for field, value in (
			("source_installation_id", "other-source"),
			("doctype_config", {"ignore_validation": 1}),
			("data", {"description": "changed"}),
		):
			envelope = example()
			envelope[field] = value
			with self.assertRaises(ProtocolError):
				validate_envelope(envelope)

	def test_invalid_envelopes(self):
		for field, value in (
			("event_sequence", 2**53 + 123),
			("event_sequence", "01"),
			("event_sequence", str(2**63)),
			("pairing_generation", "0"),
			("metadata", []),
			("metadata", None),
			("event_type", "Submit"),
			("event_type", []),
			("version", "other"),
			("created_on", "2026-10-27T10:00:00"),
			("created_on", None),
		):
			with self.subTest(field=field, value=value):
				envelope = example()
				envelope[field] = value
				envelope["payload_digest"] = payload_digest(envelope)
				with self.assertRaises(ProtocolError):
					validate_envelope(envelope)
		envelope = example()
		envelope["unknown"] = True
		with self.assertRaises(ProtocolError):
			validate_envelope(envelope)

	def test_bounded_and_unambiguous_json(self):
		for raw in (
			'[{"metadata":{},"metadata":[]}]',
			"[]",
			"{}",
			json.dumps([example()] * 51),
			json.dumps([{**example(), "data": {"huge": "x" * 1_048_576}}]),
			"[" * 1500 + "]" * 1500,
		):
			with self.assertRaises(ProtocolError):
				decode_batch(raw)
		envelope = example()
		deep = envelope["data"]
		for _ in range(40):
			deep["nested"] = {}
			deep = deep["nested"]
		with self.assertRaises(ProtocolError):
			validate_envelope(envelope)

	def test_non_finite_and_python_objects_rejected(self):
		for value in (float("nan"), float("inf"), {1, 2}, b"binary"):
			envelope = example()
			envelope["data"]["invalid"] = value
			with self.assertRaises(ProtocolError):
				validate_envelope(envelope)
