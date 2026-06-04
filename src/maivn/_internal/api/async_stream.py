"""Async helpers shared by API classes."""

# pyright: strict
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import TypeAlias

from maivn._internal.core.entities.sse_event import SSEEvent

# MARK: Types


class _StreamEnd:
    """Sentinel queued after the worker has drained the sync iterator."""


StreamQueueItem: TypeAlias = SSEEvent | BaseException | _StreamEnd


# MARK: Thread Bridge


async def stream_in_worker_thread(
    stream_factory: Callable[[], Iterator[SSEEvent]],
) -> AsyncIterator[SSEEvent]:
    """Yield a sync stream iterator from a worker thread into the active event loop."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StreamQueueItem] = asyncio.Queue()
    sentinel = _StreamEnd()

    def _drain() -> None:
        try:
            for event in stream_factory():
                asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()
        except Exception as exc:  # noqa: BLE001 - stream errors cross the worker-thread queue
            asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop).result()

    worker = loop.run_in_executor(None, _drain)
    try:
        while True:
            item = await queue.get()
            if isinstance(item, _StreamEnd):
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        await worker


# MARK: Exports

__all__ = ["stream_in_worker_thread"]
