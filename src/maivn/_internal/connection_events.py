"""Bounded standard-library form transport for the canonical signed receiver."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from maivn_contracts.scenarios import FormSubmittedEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

_ATTEMPTS = 3
_ASCII_SPACE = 32
_RETRYABLE = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class FormPublishReceipt:
    """Durable receiver acceptance and caller correlation, not agent completion."""

    accepted: bool
    form_id: str
    submission_id: str
    delivery_id: str
    status_code: int


class FormPublishError(RuntimeError):
    """Safe transport refusal without request fields, credentials or response text."""

    def __init__(self, status_code: int | None) -> None:
        """Retain only the HTTP status, or None for a connection failure."""
        self.status_code = status_code
        message = (
            f'Form receiver refused the submission (HTTP {status_code}).'
            if status_code is not None
            else 'Form receiver could not be reached after bounded retries.'
        )
        super().__init__(message)


class FormPublisher:
    """Send selected fields from a backend, never from browser signing code.

    ``receiver_url`` is the full saved connection callback URL. HTTPS is required
    except for literal localhost, 127.0.0.1 or ::1 development receivers. Redirects
    are never followed. Three attempts reuse the exact body and submission time;
    only the HMAC signing timestamp is refreshed. Reuse your submission ID after
    an uncertain response: the receiver keeps the first accepted submission.
    """

    def __init__(self, *, receiver_url: str, connection_id: str, secret: str) -> None:
        """Configure an explicit receiver and a privately obtained signing secret."""
        self._scheme, self._host, self._port, self._path = receiver_address(
            receiver_url, connection_id
        )
        self._secret = _signing_secret(secret)

    def submit(
        self, form_id: str, submission_id: str, fields: Mapping[str, object]
    ) -> FormPublishReceipt:
        """Submit one stable caller identity and return accepted delivery correlation."""
        event = FormSubmittedEvent.model_validate(
            {
                'form_id': form_id,
                'submission_id': submission_id,
                'submitted_at': datetime.now(timezone.utc),
                'fields': fields,
            }
        )
        body = json.dumps(
            event.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':')
        ).encode('utf-8')
        delivery_id = hashlib.sha256(f'form\0{form_id}\0{submission_id}'.encode()).hexdigest()
        status: int | None = None
        for attempt in range(_ATTEMPTS):
            try:
                status = self._send(body)
            except (OSError, http.client.HTTPException):
                status = None
            if status == HTTPStatus.ACCEPTED:
                return FormPublishReceipt(
                    accepted=True,
                    form_id=form_id,
                    submission_id=submission_id,
                    delivery_id=delivery_id,
                    status_code=status,
                )
            if status is not None and status not in _RETRYABLE:
                raise FormPublishError(status)
            if attempt + 1 < _ATTEMPTS:
                time.sleep(0.2 * (attempt + 1))
        raise FormPublishError(status)

    def _send(self, body: bytes) -> int:
        timestamp = str(int(time.time()))
        signed = b'POST.' + self._path.encode() + b'.' + timestamp.encode() + b'.' + body
        digest = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        connection_type = (
            http.client.HTTPSConnection if self._scheme == 'https' else http.client.HTTPConnection
        )
        connection = connection_type(self._host, self._port, timeout=10)
        try:
            connection.request(
                'POST',
                self._path,
                body,
                {
                    'Content-Type': 'application/json',
                    'X-Maivn-Signature': f't={timestamp},v1={digest}',
                },
            )
            response = connection.getresponse()
            # Acceptance is the canonical status. Never expose arbitrary provider text,
            # follow Location, or buffer an unbounded response body.
            return response.status
        finally:
            connection.close()


def _signing_secret(secret: object) -> bytes:
    if not isinstance(secret, str) or not secret:
        message = 'a nonempty form signing secret is required'
        raise ValueError(message)
    return secret.encode('utf-8')


def receiver_address(url: object, connection_id: object) -> tuple[str, str, int, str]:
    """Validate a canonical connection receiver and return its transport address."""
    message = 'receiver URL must be the exact connection events URL, using HTTPS or local HTTP'
    if (
        not isinstance(connection_id, str)
        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', connection_id)
        or not isinstance(url, str)
        or any(ord(char) <= _ASCII_SPACE or char == '\\' for char in url)
        or '?' in url
        or '#' in url
    ):
        raise ValueError(message)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError(message) from None
    path = f'/v1/connections/{connection_id}/events'
    host = parsed.hostname
    if (
        parsed.scheme not in {'https', 'http'}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != path
        or port == 0
        or (parsed.scheme == 'http' and host not in {'localhost', '127.0.0.1', '::1'})
    ):
        raise ValueError(message)
    return parsed.scheme, host, port or (443 if parsed.scheme == 'https' else 80), path
