"""Sync facade helpers for async SDK streams.

Adapted from the v1 async-stream bridge. The direction is inverted for v2:
the SDK owns an async HTTP core and exposes a synchronous iterator facade for
the v1-style ``for event in swarm.stream(...)`` surface.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from queue import Full, Queue
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, TypeAlias, TypeVar

from maivn._internal.errors import AsyncContextError
from maivn._internal.models import StreamEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine, Generator


class _StreamEnd:
    """Sentinel queued after the async iterator drains."""


StreamQueueItem: TypeAlias = StreamEvent | BaseException | _StreamEnd


_STREAM_QUEUE_MAX_SIZE = 1
_WORKER_QUEUE_WAIT_SECONDS = 0.01
_WORKER_JOIN_TIMEOUT_SECONDS = 1.0


T = TypeVar('T')
_tool_loop: ContextVar[asyncio.AbstractEventLoop | None] = ContextVar(
    'maivn_sync_tool_loop', default=None
)


@contextmanager
def owning_tool_loop() -> Generator[None, None, None]:
    """Route nested sync SDK calls from worker threads back to the invocation loop."""
    token = _tool_loop.set(asyncio.get_running_loop())
    try:
        yield
    finally:
        _tool_loop.reset(token)


def run_blocking(factory: Callable[[], Coroutine[object, object, T]]) -> T:
    """Run an async SDK method from a synchronous facade."""
    _raise_if_loop_is_running()
    loop = _tool_loop.get()
    if loop is not None:
        if not loop.is_running():
            message = 'The owning SDK invocation is no longer running'
            raise AsyncContextError(message)
        return asyncio.run_coroutine_threadsafe(factory(), loop).result()
    return asyncio.run(factory())


class _StreamBridge:
    """Coordinate a cancellable async producer with a synchronous consumer."""

    def __init__(self, stream_factory: Callable[[], AsyncIterator[StreamEvent]]) -> None:
        self._stream_factory = stream_factory
        self._queue: Queue[StreamQueueItem] = Queue(maxsize=_STREAM_QUEUE_MAX_SIZE)
        self._sentinel = _StreamEnd()
        self._close_requested = Event()
        self._control_lock = Lock()
        self._drain_loop: asyncio.AbstractEventLoop | None = None
        self._drain_task: asyncio.Task[object] | None = None
        self._space_available: asyncio.Event | None = None
        self._worker: Thread | None = None

    def start(self) -> None:
        """Start the producer with the caller's context variables."""
        caller_context = copy_context()
        self._worker = Thread(
            target=lambda: caller_context.run(self._run),
            name='maivn-sdk-stream',
            daemon=True,
        )
        self._worker.start()

    def get(self) -> StreamQueueItem:
        """Wait for one producer item."""
        item = self._queue.get()
        self._notify_space_available()
        return item

    def close(self) -> None:
        """Cancel the producer and return after a bounded worker wait."""
        self._cancel_drain()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=_WORKER_JOIN_TIMEOUT_SECONDS)

    def _enqueue_from_worker(self, item: StreamQueueItem) -> None:
        while not self._close_requested.is_set():
            try:
                self._queue.put(item, timeout=_WORKER_QUEUE_WAIT_SECONDS)
            except Full:
                continue
            return

    def _cancel_drain(self) -> None:
        self._close_requested.set()
        with self._control_lock:
            loop = self._drain_loop
            task = self._drain_task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                return

    def _notify_space_available(self) -> None:
        with self._control_lock:
            loop = self._drain_loop
            space_available = self._space_available
        if loop is not None and space_available is not None:
            try:
                loop.call_soon_threadsafe(space_available.set)
            except RuntimeError:
                return

    async def _enqueue_from_async(self, item: StreamQueueItem) -> bool:
        while not self._close_requested.is_set():
            if not self._queue.full():
                self._queue.put_nowait(item)
                return True
            space_available = self._space_available
            if space_available is None:
                message = 'The async stream worker did not initialize its queue signal'
                raise RuntimeError(message)
            space_available.clear()
            if not self._queue.full():
                continue
            await space_available.wait()
        return False

    async def _drain(self) -> None:
        task = asyncio.current_task()
        if task is None:
            message = 'The async stream worker did not create a task'
            raise RuntimeError(message)
        with self._control_lock:
            self._drain_loop = asyncio.get_running_loop()
            self._drain_task = task
            self._space_available = asyncio.Event()
            should_cancel = self._close_requested.is_set()
        if should_cancel:
            return
        source = self._stream_factory()
        try:
            async for event in source:
                if not await self._enqueue_from_async(event):
                    return
        except BaseException as exc:  # noqa: BLE001 - errors cross the thread boundary.
            await self._enqueue_from_async(exc)
        finally:
            try:
                close = getattr(source, 'aclose', None)
                if callable(close):
                    closing = close()
                    if inspect.isawaitable(closing):
                        await closing
            finally:
                await self._enqueue_from_async(self._sentinel)

    def _run(self) -> None:
        try:
            run_blocking(self._drain)
        except BaseException as exc:  # noqa: BLE001 - cancellation crosses the thread boundary.
            self._enqueue_from_worker(exc)
            self._enqueue_from_worker(self._sentinel)


def stream_async_iterator(
    stream_factory: Callable[[], AsyncIterator[StreamEvent]],
) -> Generator[StreamEvent, None, None]:
    """Yield an async stream iterator through a worker thread.

    Call ``close()`` (or use ``contextlib.closing``) when stopping early. A plain
    ``break`` leaves a retained Python generator open, so it cannot release the
    async source until the caller closes or discards the iterator. ``close()``
    waits up to one second; a source that does not finish cancellation in that
    time can outlive the limit in the daemon worker.
    """
    _raise_if_loop_is_running()
    bridge = _StreamBridge(stream_factory)
    bridge.start()
    try:
        while True:
            item = bridge.get()
            if isinstance(item, _StreamEnd):
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        bridge.close()


def _raise_if_loop_is_running() -> None:
    try:
        _ = asyncio.get_running_loop()
    except RuntimeError:
        return
    message = 'Use the async SDK method when already running inside an event loop'
    raise AsyncContextError(message)


__all__ = ['run_blocking', 'stream_async_iterator']
