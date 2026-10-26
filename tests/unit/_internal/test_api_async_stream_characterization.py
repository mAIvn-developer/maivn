"""Characterization tests for the synchronous facade over SDK async streams."""

from __future__ import annotations

import asyncio
import time
from contextvars import Context, copy_context
from typing import TYPE_CHECKING

import pytest

from maivn import AsyncContextError, StreamEvent
from maivn._internal.api.async_stream import (
    close_async_iterator,
    owning_tool_loop,
    run_blocking,
    stream_async_iterator,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LATE_CONSUMER_PAUSE_SECONDS = 0.2


def _event(position: int) -> StreamEvent:
    return StreamEvent(position=position, event_type='status', data={'payload': {}})


async def _value() -> str:
    return 'value'


async def _events() -> AsyncIterator[StreamEvent]:
    yield _event(1)


class _PlainIterator:
    """Async iterator without aclose, the minimal protocol a source may implement."""

    def __init__(self, count: int) -> None:
        self._remaining = list(range(1, count + 1))

    def __aiter__(self) -> _PlainIterator:
        return self

    async def __anext__(self) -> StreamEvent:
        if not self._remaining:
            raise StopAsyncIteration
        return _event(self._remaining.pop(0))


class _SyncCloseIterator(_PlainIterator):
    """Async iterator whose aclose is synchronous and records the call."""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.closed = False

    def aclose(self) -> None:
        self.closed = True


def test_sync_facades_refuse_to_run_inside_an_event_loop() -> None:
    """Both facades direct callers to the async API when a loop is already running."""

    async def inside_loop() -> tuple[str, str]:
        with pytest.raises(AsyncContextError) as blocking:
            _ = run_blocking(_value)
        with pytest.raises(AsyncContextError) as streaming:
            _ = next(stream_async_iterator(_events))
        return str(blocking.value), str(streaming.value)

    messages = asyncio.run(inside_loop())

    assert messages == ('Use the async SDK method when already running inside an event loop',) * 2


def test_run_blocking_without_owning_loop_runs_its_own_loop() -> None:
    """Outside any loop, run_blocking drives the coroutine to completion."""
    assert run_blocking(_value) == 'value'


def _context_with_finished_owning_loop() -> Context:
    async def capture() -> Context:
        with owning_tool_loop():
            return copy_context()

    return asyncio.run(capture())


def test_facades_bound_to_a_finished_invocation_loop_raise() -> None:
    """A context that still names a stopped owning loop cannot run nested SDK work."""
    context = _context_with_finished_owning_loop()

    with pytest.raises(
        AsyncContextError, match=r'^The owning SDK invocation is no longer running$'
    ):
        _ = context.run(run_blocking, _value)
    with pytest.raises(
        AsyncContextError, match=r'^The owning SDK invocation is no longer running$'
    ):
        _ = context.run(lambda: list(stream_async_iterator(_events)))


def test_sources_without_async_close_are_streamed_completely() -> None:
    """Sources lacking aclose, or with a synchronous aclose, still stream every event."""
    sync_close = _SyncCloseIterator(2)

    plain = [event.position for event in stream_async_iterator(lambda: _PlainIterator(2))]
    closing = [event.position for event in stream_async_iterator(lambda: sync_close)]

    assert plain == [1, 2]
    assert closing == [1, 2]
    assert sync_close.closed is True


def test_late_consumer_finishes_after_the_worker_loop_has_closed() -> None:
    """Reading the end marker after the worker finished still ends iteration cleanly."""
    iterator = stream_async_iterator(_events)

    first = next(iterator)
    time.sleep(_LATE_CONSUMER_PAUSE_SECONDS)
    rest = list(iterator)

    assert first.position == 1
    assert rest == []


def test_close_async_iterator_tolerates_sources_without_async_close() -> None:
    """Closing accepts sources with no aclose and with a synchronous aclose."""
    sync_close = _SyncCloseIterator(0)

    async def close_both() -> None:
        await close_async_iterator(_PlainIterator(0))
        await close_async_iterator(sync_close)

    asyncio.run(close_both())

    assert sync_close.closed is True
