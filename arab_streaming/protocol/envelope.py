"""Pure wire validation. This module does not authenticate or persist messages."""

import hashlib
import hmac
import json
import math
import re
from datetime import datetime

from arab_streaming.protocol.version import (
	MAX_BATCH_BYTES,
	MAX_BATCH_EVENTS,
	MAX_EVENT_BYTES,
	MAX_JSON_DEPTH,
	MAX_JSON_NODES,
	MAX_SEQUENCE,
	PROTOCOL,
	VERSION,
)

FIELDS = frozenset(
	{
		"protocol",
		"version",
		"source_installation_id",
		"pairing_id",
		"pairing_generation",
		"event_id",
		"event_sequence",
		"created_on",
		"created_by",
		"reference_doctype",
		"reference_name",
		"event_type",
		"document_event",
		"data",
		"metadata",
		"doctype_config",
		"payload_digest",
	}
)
OPERATIONS = frozenset({"Create", "Update", "Cancel", "Delete", "Rename"})


class ProtocolError(ValueError):
	pass


def _bounded_json(value):
	stack = [(value, 0)]
	count = 0
	while stack:
		item, depth = stack.pop()
		count += 1
		if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
			raise ProtocolError("JSON structure exceeds protocol limits")
		if isinstance(item, dict):
			if count + len(stack) + len(item) > MAX_JSON_NODES:
				raise ProtocolError("JSON structure exceeds protocol limits")
			if any(not isinstance(key, str) for key in item):
				raise ProtocolError("JSON object keys must be strings")
			stack.extend((child, depth + 1) for child in item.values())
		elif isinstance(item, list):
			if count + len(stack) + len(item) > MAX_JSON_NODES:
				raise ProtocolError("JSON structure exceeds protocol limits")
			stack.extend((child, depth + 1) for child in item)
		elif isinstance(item, float):
			if not math.isfinite(item):
				raise ProtocolError("Non-finite numbers are not supported")
		elif item is not None and not isinstance(item, (str, int, bool)):
			raise ProtocolError("Value is not JSON data")


def canonical_json(value):
	_bounded_json(value)
	try:
		return json.dumps(
			value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
		).encode("utf-8")
	except (ValueError, UnicodeError) as exc:
		raise ProtocolError("Invalid JSON text") from exc


def payload_digest(envelope):
	return hashlib.sha256(
		canonical_json({key: value for key, value in envelope.items() if key != "payload_digest"})
	).hexdigest()


def _sequence(value):
	if (
		not isinstance(value, str)
		or not re.fullmatch(r"[1-9][0-9]{0,18}", value)
		or int(value) > MAX_SEQUENCE
	):
		raise ProtocolError("Sequence and generation must be positive signed-64-bit decimal strings")


def validate_envelope(envelope):
	if not isinstance(envelope, dict) or envelope.keys() != FIELDS:
		raise ProtocolError("Envelope has missing or unsupported fields")
	if envelope["protocol"] != PROTOCOL or envelope["version"] != VERSION:
		raise ProtocolError("Unsupported Arab-ERP Streaming protocol")
	if len(canonical_json(envelope)) > MAX_EVENT_BYTES:
		raise ProtocolError("Event exceeds protocol byte limit")
	for key in (
		"source_installation_id",
		"pairing_id",
		"event_id",
		"created_by",
		"reference_doctype",
		"reference_name",
		"document_event",
	):
		value = envelope[key]
		if not isinstance(value, str) or not 1 <= len(value) <= 140 or any(ord(char) < 32 for char in value):
			raise ProtocolError("Invalid envelope identity or attribution")
	_sequence(envelope["event_sequence"])
	_sequence(envelope["pairing_generation"])
	if not isinstance(envelope["event_type"], str) or envelope["event_type"] not in OPERATIONS:
		raise ProtocolError("Unsupported event type")
	for key in ("data", "metadata", "doctype_config"):
		if not isinstance(envelope[key], dict):
			raise ProtocolError("Event data, metadata and configuration must be objects")
	try:
		date = datetime.fromisoformat(envelope["created_on"].replace("Z", "+00:00"))
		if date.utcoffset() is None:
			raise ValueError
	except (AttributeError, TypeError, ValueError) as exc:
		raise ProtocolError("Source timestamp must include an explicit UTC offset") from exc
	digest = envelope["payload_digest"]
	if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
		raise ProtocolError("Invalid payload digest")
	if not hmac.compare_digest(digest, payload_digest(envelope)):
		raise ProtocolError("Payload digest does not match envelope")
	return envelope


def _unique_object(pairs):
	result = {}
	for key, value in pairs:
		if key in result:
			raise ProtocolError("Duplicate JSON object key")
		result[key] = value
	return result


def decode_batch(raw):
	if not isinstance(raw, (str, bytes)):
		raise ProtocolError("Expected encoded JSON batch")
	try:
		encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
		if len(encoded) > MAX_BATCH_BYTES:
			raise ProtocolError("Batch exceeds protocol byte limit")
		batch = json.loads(encoded, object_pairs_hook=_unique_object)
	except (ValueError, UnicodeError, RecursionError) as exc:
		raise ProtocolError("Invalid JSON batch") from exc
	if not isinstance(batch, list) or not 1 <= len(batch) <= MAX_BATCH_EVENTS:
		raise ProtocolError("Batch must contain between 1 and 50 events")
	_bounded_json(batch)
	return [validate_envelope(envelope) for envelope in batch]
