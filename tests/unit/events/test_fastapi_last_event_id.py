"""The SSE endpoint resumes from the standard ``Last-Event-ID`` header (U05-D20)."""

from __future__ import annotations

import asyncio
from typing import cast

import httpx
import pytest

from maivn.events import BridgeRegistry, EventBridge

_ = pytest.importorskip('fastapi')
_ = pytest.importorskip('sse_starlette')

from fastapi import FastAPI  # noqa: E402

from maivn.events.fastapi import get_event_bridge, mount_events  # noqa: E402


def _event_names(body: str) -> list[str]:
    names: list[str] = []
    for line in body.replace('\r\n', '\n').split('\n'):
        field, _, value = line.partition(':')
        if field == 'event':
            names.append(value.strip())
    return names


def _resume(*, header: str | None, query: str | None) -> list[str]:
    """Emit three events, reconnect with the given cursors and return the event names sent."""
    registry = BridgeRegistry()
    app = FastAPI()
    _ = mount_events(app, registry=registry)
    bridge: EventBridge = get_event_bridge('s1', registry=registry)
    names: list[str] = []

    async def scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_status_message('a', 'two')
        await bridge.emit_final('three')
        ids = [cast('str', event['id']) for event in bridge.get_history()]
        cursors = {'first': ids[0], 'second': ids[1]}
        headers = {} if header is None else {'Last-Event-ID': cursors[header]}
        params = {} if query is None else {'last_event_id': cursors[query]}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            response = await client.get('/maivn/events/s1', headers=headers, params=params)
        names.extend(_event_names(response.text))

    asyncio.run(scenario())
    return names


def test_the_last_event_id_header_skips_events_already_seen() -> None:
    """A browser EventSource reconnect sends only the header."""
    assert _resume(header='second', query=None) == ['final']


def test_the_header_wins_over_the_query_parameter() -> None:
    """A reconnect keeps the original URL's query; the header carries the newer cursor."""
    assert _resume(header='second', query='first') == ['final']


def test_the_query_parameter_still_works_without_the_header() -> None:
    """Clients that cannot set headers keep resuming through the query parameter."""
    assert _resume(header=None, query='first') == ['status_message', 'final']


def test_without_any_cursor_the_whole_history_is_replayed() -> None:
    """A first connection receives every buffered event."""
    assert _resume(header=None, query=None) == ['status_message', 'status_message', 'final']
