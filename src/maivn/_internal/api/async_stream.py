"""Async helpers shared by API classes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from maivn._internal.core.entities.sse_event import SSEEvent

# MARK: Thread Bridge


async def stream_in_worker_thread(
    stream_factory: Callable[[], Iterator[SSEEvent]],
) -> AsyncIterator[SSEEvent]:
    """Yield a sync stream iterator from a worker thread into the active event loop."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    sentinel = object()

    def _drain() -> None:
        try:
            for event in stream_factory():
                asyncio.run_coroutine_threadsafe(queue.put(event), loop).result()
        except Exception as exc:  # noqa: BLE001
            asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(sentinel), loop).result()

    worker = loop.run_in_executor(None, _drain)
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        await worker


__all__ = ["stream_in_worker_thread"]
