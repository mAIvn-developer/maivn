"""Coordinate transient HTTP clients sharing one injected connection pool."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from threading import Lock
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

    from typing_extensions import Self


class _PoolState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.users = 0
        self.closing = False
        self.failed = False
        self.idle: Future[None] | None = None

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            with self.lock:
                if self.failed:
                    message = 'Injected HTTP transport cleanup failed'
                    raise RuntimeError(message)
                if self.loop is None:
                    self.loop = loop
                    self.idle = Future()
                if self.loop is loop and not self.closing:
                    self.users += 1
                    return
                idle = self.idle
            if idle is not None:
                # HTTP connection pools cannot move open sockets between loops.
                # Shield the shared signal from a cancelled individual waiter.
                await asyncio.shield(asyncio.wrap_future(idle))

    async def release(self, transport: httpx.AsyncBaseTransport) -> None:
        with self.lock:
            self.users -= 1
            if self.users:
                return
            self.closing = True
            idle = self.idle
        cleanup = asyncio.create_task(self._close(transport, idle))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:  # noqa: PERF203 - cancellation must await pool cleanup.
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self, transport: httpx.AsyncBaseTransport, idle: Future[None] | None) -> None:
        try:
            await transport.aclose()
        except BaseException:
            with self.lock:
                self.failed = True
            raise
        finally:
            with self.lock:
                self.loop = None
                self.closing = False
                self.idle = None
            if idle is not None:
                idle.set_result(None)


class LoopLocalTransport(httpx.AsyncBaseTransport):
    """SDK-owned pooling: one connection pool per event loop.

    One pool shared across loops makes a second loop wait until the first
    releases it. A synchronous SDK call runs its own loop while a streamed run
    holds the first one open, so a human-in-the-loop answer sent mid-run waited
    for the stream it was meant to resume, until the server timed the run out.
    Separate pools never wait on each other; each is still leased and closed
    by its own loop.
    """

    def __init__(
        self, factory: Callable[[], httpx.AsyncBaseTransport] = httpx.AsyncHTTPTransport
    ) -> None:
        self._factory = factory
        self._pools: WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncBaseTransport] = (
            WeakKeyDictionary()
        )
        self._lock = Lock()

    def for_current_loop(self) -> httpx.AsyncBaseTransport:
        """Return the running loop's pool, or an unshared one outside a loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._factory()
        with self._lock:
            pool = self._pools.get(loop)
            if pool is None:
                pool = self._pools[loop] = self._factory()
            return pool

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.for_current_loop().handle_async_request(request)

    async def aclose(self) -> None:
        await self.for_current_loop().aclose()


_states: WeakKeyDictionary[httpx.AsyncBaseTransport, _PoolState] = WeakKeyDictionary()
_states_lock = Lock()


class _TransportLease(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport, state: _PoolState) -> None:
        self._transport = transport
        self._state = state
        self._start_lock = asyncio.Lock()
        self._acquired = False
        self._closed = False

    async def _acquire(self) -> None:
        async with self._start_lock:
            if self._closed:
                message = 'HTTP transport lease is closed'
                raise RuntimeError(message)
            if not self._acquired:
                await self._state.acquire()
                try:
                    await self._transport.__aenter__()
                except BaseException:
                    await self._state.release(self._transport)
                    raise
                self._acquired = True

    async def __aenter__(self) -> Self:
        await self._acquire()
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await self._acquire()
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        async with self._start_lock:
            if self._closed:
                return
            self._closed = True
            if self._acquired:
                self._acquired = False
                await self._state.release(self._transport)


def lease_transport(transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncBaseTransport | None:
    """Close an injected pool only after its last transient client finishes.

    Same-loop clients share connections. Different loops wait for the owning
    loop to close its connections first, including repeated synchronous SDK calls,
    except for a `LoopLocalTransport`, which gives each loop its own pool.
    Default transports remain owned directly by their individual HTTP clients.
    """
    if transport is None:
        return None
    if isinstance(transport, LoopLocalTransport):
        transport = transport.for_current_loop()
    with _states_lock:
        state = _states.setdefault(transport, _PoolState())
    return _TransportLease(transport, state)
