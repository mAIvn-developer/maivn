"""Exactly-once telemetry publication from every public client stream."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
from pydantic import AnyUrl

from maivn import Client, ClientConfig
from maivn.telemetry import register_listener
from maivn.telemetry._registry import reset_listeners_for_testing

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from maivn._internal.models import StreamEvent

EXPECTED_EVENT_COUNT = 2


def setup_function() -> None:
    """Start each delivery test with no process-wide listeners."""
    reset_listeners_for_testing()


def teardown_function() -> None:
    """Remove process-wide listeners after each delivery test."""
    reset_listeners_for_testing()


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        auto_detect_timezone=False,
    )


def _sse(*, session_id: str, thread_id: str | None = None) -> httpx.Response:
    common: dict[str, object] = {
        'session_id': session_id,
        'root_event_id': 'evt-root',
        'ts': '2026-09-01T12:00:00Z',
    }
    if thread_id is not None:
        common['thread_id'] = thread_id
    events = [
        (
            'progress_update',
            {
                **common,
                'event_id': 'evt-progress',
                'type': 'progress_update',
                'payload': {'status': 'running', 'message': 'DO-NOT-PUBLISH'},
            },
        ),
        (
            'final',
            {
                **common,
                'event_id': 'evt-final',
                'type': 'final',
                'payload': {
                    'message': {
                        'message_id': 'msg-final',
                        'role': 'assistant',
                        'content': 'done',
                        'ts': '2026-09-01T12:00:00Z',
                    },
                    'usage': {'input_tokens': 3, 'output_tokens': 2},
                },
            },
        ),
    ]
    content = ''.join(
        f'id: {position}\nevent: {name}\ndata: {json.dumps(data)}\n\n'
        for position, (name, data) in enumerate(events, start=1)
    )
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=content,
    )


async def _collect(events: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in events]


def test_astream_publishes_processed_events_exactly_once() -> None:
    """The invoke path must not republish via its public session helper."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-new', 'stream_position': 0},
            )
        assert request.url.path == '/v1/sessions/ses-new/events'
        return _sse(session_id='ses-new')

    observed: list[tuple[str, int]] = []
    register_listener(lambda event: observed.append((event.event_name, event.position)))

    returned = asyncio.run(_collect(_client(handler).astream('hello')))

    assert [event.event_type for event in returned] == ['progress_update', 'final']
    assert observed == [('progress_update', 1), ('final', 2)]


def test_direct_stream_session_publishes_each_event_once() -> None:
    """A direct Client session stream is observable without an Agent wrapper."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/sessions/ses-direct/events'
        return _sse(session_id='ses-direct')

    observed: list[str] = []
    register_listener(lambda event: observed.append(event.event_name))

    returned = asyncio.run(_collect(_client(handler).stream_session('ses-direct')))

    assert len(returned) == EXPECTED_EVENT_COUNT
    assert observed == ['progress_update', 'final']


def test_direct_thread_events_publish_each_event_once() -> None:
    """Thread replay streams use the same public telemetry contract."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/threads/thr-direct/events'
        return _sse(session_id='ses-thread', thread_id='thr-direct')

    observed: list[str] = []
    register_listener(lambda event: observed.append(event.event_name))

    returned = asyncio.run(_collect(_client(handler).athread_events('thr-direct')))

    assert len(returned) == EXPECTED_EVENT_COUNT
    assert observed == ['progress_update', 'final']
