import json

import frappe
import requests

from arab_streaming.auth import WIRE_METHODS
from arab_streaming.protocol.version import PROTOCOL, VERSION
from arab_streaming.streaming.validation import normalize_site_url


class RemoteError(frappe.ValidationError):
	pass


def remote_call(connection, method, **values):
	if method not in WIRE_METHODS:
		raise RemoteError("Unsupported streaming endpoint")
	url = normalize_site_url(connection.domain)
	if connection.doctype == "Slave Instance":
		from arab_streaming.streaming.connections import peer_state

		state = peer_state(connection.doctype, connection.name)
		access, secret = state.remote_access_key, state.get_password("remote_secret", raise_exception=False)
		if not access or not secret:
			raise RemoteError("Register this branch receiving endpoint before pushing")
		authorization = f"ArabStreamingPeer {access}:{secret}"
	else:
		authorization = f"ArabStreaming {connection.access_key}:{connection.get_password('secret')}"
	data = {"protocol": PROTOCOL, "version": VERSION, **values}
	raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
	if len(raw) > 4_194_304:
		raise RemoteError("Streaming request exceeds the batch size limit")
	try:
		with requests.post(
			url + "/api/method/" + method,
			data=raw,
			headers={
				"Authorization": authorization,
				"Content-Type": "application/json",
				"Accept": "application/json",
			},
			timeout=(5, 45),
			allow_redirects=False,
			stream=True,
		) as response:
			chunks, size = [], 0
			limit = 4_194_304 if response.status_code == 200 else 65_536
			for chunk in response.iter_content(65536):
				size += len(chunk)
				if size > limit:
					raise RemoteError(
						f"Remote streaming response exceeds its size limit (HTTP {response.status_code})"
					)
				chunks.append(chunk)
			if response.status_code != 200:
				detail = ""
				try:
					error = json.loads(b"".join(chunks))
					if isinstance(error, dict) and error.get("exc_type") in (
						"PermissionError",
						"ValidationError",
						"LinkValidationError",
						"DoesNotExistError",
						"MandatoryError",
					):
						message = error.get("exception")
						if isinstance(message, str):
							detail = message.split(": ", 1)[-1][:1500]
				except (ValueError, TypeError):
					pass
				raise RemoteError(
					f"Remote streaming request rejected (HTTP {response.status_code}): "
					+ (detail or "check credentials, role and branch permissions")
				)
			result = json.loads(b"".join(chunks))
	except requests.RequestException:
		raise RemoteError(
			"Unable to reach the master over verified HTTPS; check its URL, certificate and network"
		) from None
	except (ValueError, TypeError):
		raise RemoteError("The master returned an invalid streaming response") from None
	message = result.get("message") if isinstance(result, dict) else None
	if not isinstance(message, dict):
		raise RemoteError("The master does not expose a compatible streaming endpoint")
	return message
