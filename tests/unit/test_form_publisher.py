"""Form authoring and signed server-side delivery contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from maivn import Agent
from maivn._internal import connection_events as sender
from maivn.connection_events import FormPublisher, FormPublishError

if TYPE_CHECKING:
    from collections.abc import Generator

SECRET = 'form-signing-sentinel'  # noqa: S105 - Explicit synthetic signing test fixture.
PATH = '/v1/connections/conn_forms/events'
TWO_ATTEMPTS = 2
MAX_ATTEMPTS = 3


@contextmanager
def receiver(statuses: list[int]) -> Generator[tuple[str, list[tuple[bytes, str]]]]:
    """Capture actual HTTP bytes without contacting a provider."""
    captured: list[tuple[bytes, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers['Content-Length']))
            captured.append((body, self.headers['X-Maivn-Signature']))
            assert self.path == PATH
            self.send_response(statuses[min(len(captured) - 1, len(statuses) - 1)])
            self.end_headers()
            self.wfile.write(b'{"accepted":true}')

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}{PATH}', captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_form_selector_survives_where() -> None:
    """A developer filter cannot erase the mandatory form identity."""
    builder = Agent('intake', api_key='test-key').on_form('contact', connection_id='conn_forms')
    builder = builder.where('$.fields.email')
    assert builder.source.model_dump(mode='json', exclude_none=True) == {
        'kind': 'external_connection_event',
        'phase': 'phase_2',
        'connection_id': 'conn_forms',
        'event_type': 'form.submitted',
        'selector': {'kind': 'form', 'form_id': 'contact'},
    }


def test_retry_sends_immutable_body_with_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lost/failed acceptance retries preserve the original submission bytes."""
    timestamps = iter((1_000, 1_001))

    def skip_sleep(_seconds: float) -> None:
        return

    monkeypatch.setattr(
        sender, 'time', SimpleNamespace(time=lambda: next(timestamps), sleep=skip_sleep)
    )
    with receiver([503, 202]) as (url, captured):
        publisher = FormPublisher(receiver_url=url, connection_id='conn_forms', secret=SECRET)
        receipt = publisher.submit('contact', 'submission-1', {'email': 'fixture@example.invalid'})
    assert receipt.accepted is True
    assert receipt.form_id == 'contact'
    assert receipt.submission_id == 'submission-1'
    assert receipt.delivery_id == hashlib.sha256(b'form\0contact\0submission-1').hexdigest()
    assert len(captured) == TWO_ATTEMPTS
    assert captured[0][0] == captured[1][0]
    assert captured[0][1] != captured[1][1]
    for body, signature in captured:
        timestamp, actual = signature.split(',v1=')
        message = b'POST.' + PATH.encode() + b'.' + timestamp[2:].encode() + b'.' + body
        assert hmac.compare_digest(
            actual, hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
        )
        parsed = json.loads(body)
        assert parsed['form_id'] == 'contact'
        assert parsed['submission_id'] == 'submission-1'
        assert parsed['fields'] == {'email': 'fixture@example.invalid'}
        assert 'submitted_at' in parsed
    assert SECRET not in repr(publisher)
    assert SECRET not in repr(receipt)


@pytest.mark.parametrize('status', [301, 302, 307, 308, 400, 401, 403, 404, 409, 422])
def test_refusal_does_not_retry_or_follow_redirect(status: int) -> None:
    """Credentials stay on the exact receiver and refusals are reported safely."""
    with receiver([status]) as (url, captured):
        publisher = FormPublisher(receiver_url=url, connection_id='conn_forms', secret=SECRET)
        with pytest.raises(FormPublishError) as failure:
            publisher.submit('contact', 'submission-1', {})
    assert len(captured) == 1
    assert failure.value.status_code == status
    assert SECRET not in str(failure.value)


@pytest.mark.parametrize(
    'url',
    [
        'http://example.com/v1/connections/conn_forms/events',
        'https://example.com/v1/connections/other/events',
        'https://example.com/v1/connections/conn_forms/events/',
        'https://example.com/v1/connections/conn_forms/events?secret=private',
        'https://example.com/v1/connections/conn_forms/events#fragment',
        'https://example.com/v1/connections/conn_forms/events?',
        'https://example.com/v1/connections/conn_forms/events#',
        'https://user:private@example.com/v1/connections/conn_forms/events',
        'ftp://example.com/v1/connections/conn_forms/events',
        'https://example.com:invalid/v1/connections/conn_forms/events',
        'https://example.com/v1/connections/conn_forms%2fevents',
        'https://example.com/../v1/connections/conn_forms/events',
        ' https://example.com/v1/connections/conn_forms/events',
    ],
)
def test_rejects_unsafe_receiver_before_network(url: str) -> None:
    """Receiver URLs must identify the exact connection endpoint."""
    with pytest.raises(ValueError, match='receiver'):
        FormPublisher(receiver_url=url, connection_id='conn_forms', secret=SECRET)


@pytest.mark.parametrize(
    'fields',
    [
        {'attachment': {'name': 'file.pdf'}},
        {'number': float('nan')},
        {'number': float('inf')},
        {'number': 2**53},
        {'text': 'x' * 8193},
        {'list': ['x'] * 101},
        {str(index): 'x' for index in range(101)},
    ],
)
def test_invalid_fields_never_reach_receiver(fields: dict[str, object]) -> None:
    """Publisher uses the same bounded contract as the receiver."""
    with receiver([202]) as (url, captured):
        publisher = FormPublisher(receiver_url=url, connection_id='conn_forms', secret=SECRET)
        with pytest.raises(ValidationError):
            publisher.submit('contact', 'submission-1', fields)
    assert captured == []


def test_server_failure_has_bounded_attempts() -> None:
    """A receiver outage cannot loop indefinitely."""
    with receiver([503]) as (url, captured):
        publisher = FormPublisher(receiver_url=url, connection_id='conn_forms', secret=SECRET)
        with pytest.raises(FormPublishError) as failure:
            publisher.submit('contact', 'submission-1', {})
    assert len(captured) == MAX_ATTEMPTS
    assert failure.value.status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_transport_errors_are_bounded_and_value_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """An underlying HTTP error cannot leak a credential through exception chaining."""
    attempts: list[bool] = []

    def fail_request(*args: object, **kwargs: object) -> None:
        del args, kwargs
        attempts.append(True)
        raise OSError(SECRET)

    monkeypatch.setattr(sender.http.client.HTTPConnection, 'request', fail_request)
    publisher = FormPublisher(
        receiver_url='http://127.0.0.1:8200' + PATH,
        connection_id='conn_forms',
        secret=SECRET,
    )
    with pytest.raises(FormPublishError) as failure:
        publisher.submit('contact', 'submission-1', {})
    assert len(attempts) == MAX_ATTEMPTS
    assert failure.value.status_code is None
    assert failure.value.__context__ is None
    assert SECRET not in str(failure.value)
