# pyright: reportUnusedFunction=false
"""Injected pools outlive sibling clients and close before another sync loop uses them."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from http import HTTPStatus
from threading import Event, RLock
from typing import TYPE_CHECKING

import httpx
import pytest

from maivn import Agent
from maivn._internal.api.async_stream import run_blocking
from maivn._internal.errors import MaivnHTTPError
from maivn._internal.models import StreamEvent
from maivn._internal.private_artifacts import (
    _sync_byte_stream,  # pyright: ignore[reportPrivateUsage]
)
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn._internal.transport.http import HttpJsonClient

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from typing_extensions import Self


class _LoopBoundTransport(httpx.AsyncBaseTransport):
    """Model a live pool whose open connections belong to their first event loop."""

    def __init__(self) -> None:
        self.owner: asyncio.AbstractEventLoop | None = None
        self.slow_started = asyncio.Event()
        self.fast_returned = asyncio.Event()
        self.closes = 0
        self.active = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        loop = asyncio.get_running_loop()
        if self.owner is None:
            self.owner = loop
        assert self.owner is loop, 'Pooled connections escaped their event loop'
        self.active += 1
        try:
            if request.url.path == '/slow':
                self.slow_started.set()
                await self.fast_returned.wait()
                # Give the fast client's context exit a chance to close its pool.
                await asyncio.sleep(0)
                assert self.owner is loop, 'A sibling closed an active request pool'
            elif request.url.path == '/fast':
                await self.slow_started.wait()
                self.fast_returned.set()
            if request.url.path == '/broken':
                message = 'Synthetic transport read failure'
                raise httpx.ReadError(message)
            status = 403 if request.url.path == '/denied' else 200
            return httpx.Response(status, json={'ok': True})
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        self.closes += 1
        self.owner = None


def _client(transport: httpx.AsyncBaseTransport) -> HttpJsonClient:
    return HttpJsonClient(
        base_url='https://testserver', api_key='test-key', timeout_seconds=1, transport=transport
    )


def test_concurrent_clients_cannot_close_a_sibling_request_pool() -> None:
    """The first completed private preparation must not abort the other upload."""
    transport = _LoopBoundTransport()
    first, second = _client(transport), _client(transport)

    async def run() -> None:
        results = await asyncio.gather(first.post('/slow', {}), second.post('/fast', {}))
        assert all(result['ok'] for result in results)

    asyncio.run(run())
    assert transport.closes == 1
    assert transport.owner is None


def test_repeated_sync_calls_clean_pool_before_replacing_event_loop() -> None:
    """A no-op transport close would leave real sockets on an already closed loop."""
    transport = _LoopBoundTransport()
    client = _client(transport)
    assert asyncio.run(client.post('/one', {})) == {'ok': True}
    assert transport.owner is None
    assert asyncio.run(client.post('/two', {})) == {'ok': True}
    assert transport.owner is None
    expected_closes = 2
    assert transport.closes == expected_closes


@pytest.mark.parametrize('path', ['/denied', '/broken'])
def test_failed_stream_open_releases_pool_before_next_sync_call(path: str) -> None:
    """A failed send or status check cannot pin a lease to a now-closed loop."""
    transport = _LoopBoundTransport()
    client = _client(transport)
    with pytest.raises((MaivnHTTPError, httpx.ReadError)):
        asyncio.run(client.stream(path))
    assert transport.owner is None
    assert asyncio.run(client.post('/one', {})) == {'ok': True}


def test_stream_error_status_does_not_require_consuming_its_body() -> None:
    """A real streaming 404 becomes the typed HTTP error without an unbounded read."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            404,
            stream=httpx.ByteStream(b'{"reason":"not found"}'),
        )
    )
    with pytest.raises(MaivnHTTPError) as error:
        asyncio.run(_client(transport).stream('/missing'))
    assert error.value.status_code == HTTPStatus.NOT_FOUND


class _SlowCloseTransport(_LoopBoundTransport):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.finish_close = asyncio.Event()

    async def aclose(self) -> None:
        self.close_started.set()
        await self.finish_close.wait()
        await super().aclose()


class _InitializingTransport(_LoopBoundTransport):
    def __init__(self) -> None:
        super().__init__()
        self.initialized = False

    async def __aenter__(self) -> Self:
        self.initialized = True
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert self.initialized, 'Custom transport initialization was skipped'
        return await super().handle_async_request(request)


def test_custom_transport_context_initialization_remains_supported() -> None:
    """A custom transport retains the initialization AsyncClient invokes on entry."""
    transport = _InitializingTransport()
    assert asyncio.run(_client(transport).post('/one', {})) == {'ok': True}


def test_cancelled_request_finishes_pool_cleanup_before_reuse() -> None:
    """Cancellation is propagated after cleanup, without cancelling the shared idle signal."""
    transport = _SlowCloseTransport()
    client = _client(transport)

    async def run() -> None:
        first = asyncio.create_task(client.post('/one', {}))
        await transport.close_started.wait()
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()
        second = asyncio.create_task(client.post('/two', {}))
        await asyncio.sleep(0)
        assert not second.done()
        transport.finish_close.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await second == {'ok': True}

    asyncio.run(run())
    assert transport.owner is None


def test_concurrent_sync_call_waits_for_the_other_loop_to_release_its_pool() -> None:
    """One injected connection pool is never used concurrently by different loops."""
    transport = _LoopBoundTransport()
    client = _client(transport)

    async def run() -> None:
        # Hold a client lease on this loop while a synchronous call starts in a worker.
        async with client:
            assert await client.post('/one', {}) == {'ok': True}
            task = asyncio.create_task(
                asyncio.to_thread(lambda: asyncio.run(_client(transport).post('/two', {})))
            )
            await asyncio.sleep(0)
            assert not task.done()
        assert await asyncio.wait_for(task, timeout=1) == {'ok': True}

    asyncio.run(run())
    assert transport.owner is None


@pytest.mark.parametrize('streaming', [False, True])
def test_sync_tool_can_call_sdk_while_parent_stream_owns_same_transport(*, streaming: bool) -> None:
    """A worker's nested SDK call must use the owning loop instead of waiting for SSE to end."""
    transport = _LoopBoundTransport()
    parent_http, nested_http = _client(transport), _client(transport)
    private_context: ContextVar[str | None] = ContextVar('private_test_context', default=None)
    agent = Agent(name='Nested SDK reader', api_key='test-key')

    @agent.toolify(name='nested_read')
    def nested_read() -> dict[str, object]:
        assert private_context.get() == 'local-private-value'

        async def retrieve() -> dict[str, object]:
            assert private_context.get() == 'local-private-value'
            return await nested_http.post('/nested', {})

        if streaming:

            async def chunks() -> AsyncGenerator[bytes, None]:
                await retrieve()
                yield b'ok'

            return {'content': b''.join(_sync_byte_stream(chunks)).decode()}
        return run_blocking(retrieve)

    event = StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': 'nested-call',
                    'spec_ref': {'tool_id': 'nested_read', 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {},
                    'lineage': {'session_id': 'ses-parent', 'invocation_id': 'inv-parent'},
                }
            }
        },
    )

    async def run() -> None:
        runtime = LocalToolRuntime(agent.compile_tools(), private_data=None)
        private_context.set('local-private-value')
        async with parent_http:
            await parent_http.post('/one', {})
            outcome = await asyncio.wait_for(runtime.outcome_for_event(event), timeout=1)
            assert outcome is not None
            assert outcome.status == 'ok'

    asyncio.run(run())


def test_private_workspace_check_never_blocks_owner_loop_on_a_sync_worker_lock() -> None:
    """A queued custody check must not block the loop needed by the locked worker's SDK call."""
    started = Event()
    transport = _LoopBoundTransport()
    http = _client(transport)

    class Workspace:
        def __init__(self) -> None:
            self.lock = RLock()

        def sdk_private_workspace(self, _arguments: object) -> bool:
            assert self.lock.acquire(timeout=0.2), 'Custody lookup blocked the SDK owner loop'
            self.lock.release()
            return True

        def edit(self) -> dict[str, object]:
            with self.lock:
                started.set()

                async def retrieve() -> dict[str, object]:
                    await asyncio.sleep(0.05)
                    return await http.post('/nested', {})

                return run_blocking(retrieve)

    workspace = Workspace()
    agent = Agent(name='Locked workspace', api_key='test-key')
    agent.add_tool(workspace.edit, name='edit', metadata={'serialize_calls': True})
    runtime = LocalToolRuntime(agent.compile_tools(), private_data=None)

    def event(call_id: str) -> StreamEvent:
        return StreamEvent(
            position=1,
            event_type='system_tool_start',
            data={
                'payload': {
                    'tool_call': {
                        'call_id': call_id,
                        'spec_ref': {'tool_id': 'edit', 'namespace': 'sdk', 'version': 'v1'},
                        'arguments': {},
                        'lineage': {'session_id': 'session', 'invocation_id': 'invocation'},
                    }
                }
            },
        )

    async def run() -> None:
        first = asyncio.create_task(runtime.outcome_for_event(event('first')))
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(runtime.outcome_for_event(event('second')))
        outcomes = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
        assert all(outcome is not None and outcome.status == 'ok' for outcome in outcomes)

    asyncio.run(run())
