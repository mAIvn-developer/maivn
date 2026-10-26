"""Characterization of the ``maivn.events.fastapi`` SSE adapter.

The adapter mounts ``GET {prefix}{path}`` and streams a session's bridge over
Server-Sent Events. These tests drive a real FastAPI app in-process through
``httpx.ASGITransport`` (no sockets) with an isolated ``BridgeRegistry``.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from maivn.events import BridgeRegistry, EventBridge

_ = pytest.importorskip('fastapi')
_ = pytest.importorskip('sse_starlette')

# FastAPI resolves the auth hook's ``Request`` annotation at runtime.
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

from maivn.events.fastapi import (  # noqa: E402 - after the optional-dependency skip.
    create_event_router,
    get_event_bridge,
    mount_events,
    remove_event_bridge,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

AUTH_HEADER_NAME = 'x-session-key'
ALLOWED_HEADER_VALUE = 'ok'


def _run(coroutine: Coroutine[object, object, None]) -> None:
    asyncio.run(coroutine)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


def _frames(body: str) -> list[dict[str, str]]:
    """Parse an SSE body into ``{field: value}`` dicts, keeping comment lines as ``comment``."""
    frames: list[dict[str, str]] = []
    for block in body.replace('\r\n', '\n').split('\n\n'):
        if not block.strip():
            continue
        frame: dict[str, str] = {}
        for line in block.split('\n'):
            field, _, value = line.partition(':')
            frame[field or 'comment'] = value.removeprefix(' ')
        frames.append(frame)
    return frames


def _app(registry: BridgeRegistry, **kwargs: object) -> FastAPI:
    app = FastAPI()
    _ = mount_events(app, registry=registry, **kwargs)  # pyright: ignore[reportArgumentType]
    return app


# MARK: Bridge Helpers


def test_get_event_bridge_creates_frontend_safe_bridges_by_default() -> None:
    """Bridges created by the adapter target browsers unless told otherwise."""
    registry = BridgeRegistry()

    bridge = get_event_bridge('s1', registry=registry)

    assert bridge.audience == 'frontend_safe'
    assert get_event_bridge('s1', registry=registry) is bridge
    assert registry.get('s1') is bridge
    assert get_event_bridge('s2', registry=registry, audience='internal').audience == 'internal'


def test_get_event_bridge_uses_a_custom_factory() -> None:
    """A factory receives the session id and its bridge is registered."""
    registry = BridgeRegistry()
    made: list[str] = []

    def _factory(session_id: str) -> EventBridge:
        made.append(session_id)
        return EventBridge(session_id, max_history=7)

    bridge = get_event_bridge('s1', registry=registry, factory=_factory, audience='frontend_safe')

    assert made == ['s1']
    assert bridge.stream_max_history == 7  # noqa: PLR2004
    assert bridge.audience == 'internal'


def test_get_event_bridge_without_create_raises_for_unknown_sessions() -> None:
    """``create=False`` refuses to invent a bridge."""
    registry = BridgeRegistry()

    with pytest.raises(KeyError) as excinfo:
        _ = get_event_bridge('missing', create=False, registry=registry)

    assert excinfo.value.args == ("No event bridge registered for session 'missing'",)
    assert registry.get('missing') is None


def test_remove_event_bridge_closes_and_is_idempotent() -> None:
    """Removing closes the bridge, forgets it and tolerates repeats."""
    registry = BridgeRegistry()
    bridge = get_event_bridge('s1', registry=registry)

    remove_event_bridge('s1', registry=registry)
    remove_event_bridge('s1', registry=registry)

    assert bridge.stream_is_closed is True
    assert registry.get('s1') is None


def test_default_registry_is_shared_between_helpers() -> None:
    """Without a registry argument both helpers use the module-level registry."""
    session_id = 'fastapi-characterization-default-registry'
    bridge = get_event_bridge(session_id, audience='internal')
    try:
        assert get_event_bridge(session_id, create=False) is bridge
    finally:
        remove_event_bridge(session_id)

    assert bridge.stream_is_closed is True
    with pytest.raises(KeyError):
        _ = get_event_bridge(session_id, create=False)


# MARK: Router Construction


def test_router_defaults_and_overrides() -> None:
    """The router mounts one GET route under the prefix with the given tags."""
    default = create_event_router(registry=BridgeRegistry())
    custom = create_event_router(
        prefix='',
        path='/streams/{session_id}/sse',
        tags=['live'],
        registry=BridgeRegistry(),
    )

    default_route = default.routes[0]
    custom_route = custom.routes[0]
    assert isinstance(default_route, APIRoute)
    assert isinstance(custom_route, APIRoute)
    assert len(default.routes) == 1
    assert default_route.path == '/maivn/events/{session_id}'
    assert default_route.methods == {'GET'}
    assert default.tags == ['maivn-events']
    assert custom_route.path == '/streams/{session_id}/sse'
    assert custom.tags == ['live']


def test_router_requires_a_session_placeholder() -> None:
    """A path without ``{session_id}`` is rejected."""
    with pytest.raises(ValueError, match=r'^path must contain a \{session_id\} placeholder$'):
        _ = create_event_router(path='/events')


@pytest.mark.parametrize(
    ('module', 'message'),
    [
        (
            'fastapi',
            'maivn.events.fastapi requires fastapi + sse-starlette. '
            'Install with `pip install maivn[fastapi]`.',
        ),
        (
            'sse_starlette.sse',
            'maivn.events.fastapi requires sse-starlette. '
            'Install with `pip install maivn[fastapi]`.',
        ),
    ],
)
def test_missing_optional_dependency_explains_the_extra(
    module: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building a router without the optional extra names the install command."""
    monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(ModuleNotFoundError, match=f'^{re.escape(message)}$'):
        _ = create_event_router(registry=BridgeRegistry())


# MARK: Streaming Endpoint


def test_endpoint_streams_history_as_sse_frames() -> None:
    """A completed session replays every event as an SSE frame and ends."""
    registry = BridgeRegistry()
    app = _app(registry)
    bridge = get_event_bridge('s1', registry=registry)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'hi')
        await bridge.emit_final('done')
        history = bridge.get_history()
        async with _client(app) as client:
            response = await client.get('/maivn/events/s1')

        assert response.status_code == 200  # noqa: PLR2004
        assert response.headers['content-type'] == 'text/event-stream; charset=utf-8'
        frames = _frames(response.text)
        assert [(frame['id'], frame['event']) for frame in frames] == [
            (history[0]['id'], 'status_message'),
            (history[1]['id'], 'final'),
        ]
        payload = cast('dict[str, object]', json.loads(frames[1]['data']))
        assert payload['type'] == 'final'
        assert cast('dict[str, object]', payload['data'])['response'] == 'done'

    _run(_scenario())


def test_endpoint_creates_a_bridge_with_the_configured_audience_and_factory() -> None:
    """Connecting to an unknown session creates its bridge through the router settings."""
    registry = BridgeRegistry()
    created_ids: list[str] = []

    async def _scenario() -> None:
        created = asyncio.Event()

        def _factory(session_id: str) -> EventBridge:
            created_ids.append(session_id)
            created.set()
            return EventBridge(session_id, audience='internal')

        app = _app(registry, factory=_factory)

        async def _finish() -> None:
            _ = await asyncio.wait_for(created.wait(), timeout=1)
            bridge = registry.get('new')
            assert bridge is not None
            await bridge.emit_error('stop')

        async with _client(app) as client:
            response, _ = await asyncio.gather(client.get('/maivn/events/new'), _finish())

        assert [frame['event'] for frame in _frames(response.text)] == ['error']
        bridge = registry.get('new')
        assert bridge is not None
        assert bridge.audience == 'internal'

    _run(_scenario())
    assert created_ids == ['new']


def test_endpoint_honors_the_last_event_id_query_parameter() -> None:
    """``?last_event_id=`` skips events the client already received."""
    registry = BridgeRegistry()
    app = _app(registry)
    bridge = get_event_bridge('s1', registry=registry)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_final('two')
        first_id = cast('str', bridge.get_history()[0]['id'])
        async with _client(app) as client:
            response = await client.get('/maivn/events/s1', params={'last_event_id': first_id})

        assert [frame['event'] for frame in _frames(response.text)] == ['final']

    _run(_scenario())


def test_auth_dependency_guards_the_stream() -> None:
    """The auth hook runs before streaming and its HTTP errors reach the client."""
    registry = BridgeRegistry()
    seen: list[str | None] = []

    async def _auth(request: Request) -> None:
        presented = request.headers.get(AUTH_HEADER_NAME)
        seen.append(presented)
        if presented != ALLOWED_HEADER_VALUE:
            raise HTTPException(status_code=401, detail='nope')

    app = _app(registry, auth=_auth)
    bridge = get_event_bridge('s1', registry=registry)

    async def _scenario() -> None:
        await bridge.emit_final('done')
        async with _client(app) as client:
            denied = await client.get('/maivn/events/s1')
            allowed = await client.get(
                '/maivn/events/s1', headers={AUTH_HEADER_NAME: ALLOWED_HEADER_VALUE}
            )

        assert denied.status_code == 401  # noqa: PLR2004
        assert denied.json() == {'detail': 'nope'}
        assert allowed.status_code == 200  # noqa: PLR2004
        assert [frame['event'] for frame in _frames(allowed.text)] == ['final']

    _run(_scenario())
    assert seen == [None, 'ok']


def test_endpoint_heartbeat_override_sends_keepalive_comments() -> None:
    """A router-level heartbeat override emits SSE comment frames while idle."""
    registry = BridgeRegistry()
    app = _app(registry, prefix='/rt', path='/s/{session_id}', heartbeat_interval=0.01)
    bridge = get_event_bridge('s1', registry=registry)

    async def _scenario() -> None:
        async def _finish() -> None:
            await asyncio.sleep(0.1)
            await bridge.emit_final('done')

        async with _client(app) as client:
            response, _ = await asyncio.gather(client.get('/rt/s/s1'), _finish())

        frames = _frames(response.text)
        assert frames[-1]['event'] == 'final'
        keepalives = [frame for frame in frames if 'event' not in frame]
        assert keepalives
        assert all(frame['comment'].startswith('keepalive ') for frame in keepalives)

    _run(_scenario())
