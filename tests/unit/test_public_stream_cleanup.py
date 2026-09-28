"""Public synchronous stream cleanup through the SDK's wrapper layers."""

from __future__ import annotations

import asyncio
from contextlib import aclosing, closing
from http import HTTPStatus
from threading import Event
from typing import TYPE_CHECKING

import httpx
import pytest

from maivn import Agent, Client, Swarm

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator

    from maivn._internal.models import StreamEvent


_TEST_WAIT_SECONDS = 0.5
_STATUS_FRAME = b'id: 1\nevent: status\ndata: {"payload": {}}\n\n'
_MEMBER_ASSIGNMENT_FRAME = (
    b'id: 1\nevent: progress_update\ndata: '
    b'{"payload": {"action_type": "swarm_agent", "action_id": "assignment-1", '
    b'"action_name": "stream-cleanup-member", "status": "executing"}}\n\n'
)


class _BlockingSse(httpx.AsyncByteStream):
    """An SSE body that records whether the client closed it."""

    def __init__(self, frame: bytes = _STATUS_FRAME) -> None:
        self.closed = Event()
        self._released = asyncio.Event()
        self._frame = frame

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._frame
        await self._released.wait()

    async def aclose(self) -> None:
        self.closed.set()
        self._released.set()


class _FailingSse(httpx.AsyncByteStream):
    """An SSE body that proves a transport exception crosses public wrappers."""

    def __init__(self) -> None:
        self.closed = Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _STATUS_FRAME
        message = 'stream connection failed'
        raise httpx.ReadError(message)

    async def aclose(self) -> None:
        self.closed.set()


class _FiniteSse(httpx.AsyncByteStream):
    """An SSE body that ends normally after one event."""

    def __init__(self) -> None:
        self.closed = Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _STATUS_FRAME

    async def aclose(self) -> None:
        self.closed.set()


def _client(stream: httpx.AsyncByteStream) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-stream-cleanup', 'stream_position': 0},
            )
        if request.url.path == '/v1/threads/thr-stream-cleanup/time-travel':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-stream-cleanup',
                    'session_id': 'ses-stream-cleanup',
                    'stream_position': 0,
                },
            )
        assert request.url.path in {
            '/v1/sessions/ses-stream-cleanup/events',
            '/v1/threads/thr-stream-cleanup/events',
        }
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            stream=stream,
        )

    return Client(
        api_key='test-key',
        base_url='http://testserver',
        transport=httpx.MockTransport(handler),
    )


def _close_after_first_event(
    events: Generator[StreamEvent, None, None], *, event_type: str = 'status'
) -> None:
    with closing(events) as stream:
        assert next(stream).event_type == event_type


def test_client_stream_closing_releases_the_http_async_source() -> None:
    """Client.stream closes its HTTP SSE body when the sync consumer stops early."""
    source = _BlockingSse()

    _close_after_first_event(_client(source).stream('stop after first event'))

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)


def test_public_client_stream_keeps_normal_completion_and_closes_http_source() -> None:
    """A normally exhausted public stream still returns its event and releases its body."""
    source = _FiniteSse()

    events = list(_client(source).stream('finish normally'))

    assert [event.event_type for event in events] == ['status']
    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)


def test_verbose_agent_stream_closing_releases_http_source_and_marks_hook_cancelled() -> None:
    """The reporter wrapper forwards close without reporting early completion as success."""
    source = _BlockingSse()
    after_errors: list[object | None] = []
    agent = Agent(
        name='stream-cleanup-agent',
        client=_client(source),
        hook_execution_mode='scope',
    )

    def after(payload: dict[str, object | None]) -> None:
        after_errors.append(payload['error'])

    agent.after_execute = after

    _close_after_first_event(agent.stream('stop after first event', verbose=True))

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)
    assert len(after_errors) == 1
    assert isinstance(after_errors[0], GeneratorExit)


def test_verbose_swarm_stream_closing_releases_http_source_through_member_wrapper() -> None:
    """The swarm reporter and member hooks mark an early member stop as cancelled."""
    source = _BlockingSse(_MEMBER_ASSIGNMENT_FRAME)
    client = _client(source)
    member_errors: list[object | None] = []
    member = Agent(
        name='stream-cleanup-member',
        client=client,
        hook_execution_mode='scope',
    )
    swarm = Swarm(name='stream-cleanup-swarm', client=client, agents=[member])

    def member_after(payload: dict[str, object | None]) -> None:
        member_errors.append(payload['error'])

    member.after_execute = member_after

    _close_after_first_event(
        swarm.stream('stop after first event', verbose=True),
        event_type='progress_update',
    )

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)
    assert len(member_errors) == 1
    assert isinstance(member_errors[0], RuntimeError)


def test_member_after_hook_failure_does_not_prevent_http_source_cleanup() -> None:
    """A member-hook failure still releases the stream owned by the wrapper."""
    source = _BlockingSse(_MEMBER_ASSIGNMENT_FRAME)
    client = _client(source)
    member = Agent(
        name='stream-cleanup-member',
        client=client,
        hook_execution_mode='scope',
    )
    swarm = Swarm(name='hook-error-cleanup-swarm', client=client, agents=[member])

    def member_after(_payload: dict[str, object | None]) -> None:
        message = 'member cleanup hook failed'
        raise RuntimeError(message)

    member.after_execute = member_after

    _close_after_first_event(
        swarm.stream('stop after first event', verbose=True),
        event_type='progress_update',
    )

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)


def test_public_client_stream_preserves_transport_errors_and_closes_source() -> None:
    """Closing after a public stream error still releases its HTTP SSE body."""
    source = _FailingSse()
    events = _client(source).stream('stream error')

    with closing(events):
        assert next(events).event_type == 'status'
        with pytest.raises(httpx.ReadError, match='stream connection failed'):
            next(events)

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)


def test_public_async_client_stream_aclosing_releases_the_http_source() -> None:
    """Raw async consumers can explicitly close the public async stream with aclosing."""
    source = _BlockingSse()

    async def consume() -> None:
        async with aclosing(_client(source).astream('stop after first event')) as events:
            assert (await anext(events)).event_type == 'status'
        assert source.closed.is_set()

    asyncio.run(consume())

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)


def test_public_async_stream_session_aclosing_releases_http_source_before_loop_shutdown() -> None:
    """Session streams close their transport while the caller's loop is still active."""
    source = _BlockingSse()

    async def consume() -> None:
        async with aclosing(_client(source).stream_session('ses-stream-cleanup')) as events:
            assert (await anext(events)).event_type == 'status'
        assert source.closed.is_set()

    asyncio.run(consume())


def test_public_async_thread_events_aclosing_releases_http_source_before_loop_shutdown() -> None:
    """Thread-event streams close their transport while the caller's loop is active."""
    source = _BlockingSse()

    async def consume() -> None:
        async with aclosing(_client(source).athread_events('thr-stream-cleanup')) as events:
            assert (await anext(events)).event_type == 'status'
        assert source.closed.is_set()

    asyncio.run(consume())


def test_public_async_rewind_stream_aclosing_releases_http_source_before_loop_shutdown() -> None:
    """Thread rewinds close their composed invoke stream before the loop shuts down."""
    source = _BlockingSse()

    async def consume() -> None:
        async with aclosing(
            _client(source).atime_travel_thread_stream(
                'thr-stream-cleanup',
                checkpoint_id='checkpoint-cleanup',
                message='stop after first event',
            )
        ) as events:
            assert (await anext(events)).event_type == 'status'
        assert source.closed.is_set()

    asyncio.run(consume())


def test_public_async_agent_stream_aclosing_releases_http_source_and_marks_hook_cancelled() -> None:
    """Closing Agent.astream cascades through its scope-hook wrapper before return."""
    source = _BlockingSse()
    after_errors: list[object | None] = []
    agent = Agent(
        name='async-stream-cleanup-agent',
        client=_client(source),
        hook_execution_mode='scope',
    )

    def after(payload: dict[str, object | None]) -> None:
        after_errors.append(payload['error'])

    agent.after_execute = after

    async def consume() -> None:
        async with aclosing(agent.astream('stop after first event', verbose=True)) as events:
            assert (await anext(events)).event_type == 'status'
        assert source.closed.is_set()

    asyncio.run(consume())

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)
    assert len(after_errors) == 1
    assert isinstance(after_errors[0], GeneratorExit)


def test_public_async_swarm_stream_aclosing_releases_http_source_through_member_wrapper() -> None:
    """Closing Swarm.astream closes member and scope wrappers before returning."""
    source = _BlockingSse(_MEMBER_ASSIGNMENT_FRAME)
    client = _client(source)
    member_errors: list[object | None] = []
    member = Agent(
        name='stream-cleanup-member',
        client=client,
        hook_execution_mode='scope',
    )
    swarm = Swarm(name='async-stream-cleanup-swarm', client=client, agents=[member])

    def member_after(payload: dict[str, object | None]) -> None:
        member_errors.append(payload['error'])

    member.after_execute = member_after

    async def consume() -> None:
        async with aclosing(swarm.astream('stop after first event', verbose=True)) as events:
            assert (await anext(events)).event_type == 'progress_update'
        assert source.closed.is_set()

    asyncio.run(consume())

    assert source.closed.wait(timeout=_TEST_WAIT_SECONDS)
    assert len(member_errors) == 1
    assert isinstance(member_errors[0], RuntimeError)
