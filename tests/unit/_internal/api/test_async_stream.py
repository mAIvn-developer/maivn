"""Tests for the synchronous facade over SDK async streams."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from threading import Event, Thread
from time import monotonic
from typing import TYPE_CHECKING

import pytest

from maivn._internal.api.async_stream import owning_tool_loop, stream_async_iterator
from maivn._internal.models import StreamEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_TEST_WAIT_SECONDS = 0.5


def test_stream_worker_inherits_callers_context_variables() -> None:
    """Preserve Studio's reporter context inside the SDK stream worker thread."""
    marker: ContextVar[str] = ContextVar('stream_test_marker', default='missing')
    token = marker.set('studio')

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            position=1,
            event_type='status',
            data={'payload': {'context_value': marker.get()}},
        )

    try:
        streamed = list(stream_async_iterator(events))
    finally:
        marker.reset(token)

    assert streamed[0].payload['context_value'] == 'studio'


def test_stream_uses_the_owning_tool_loop_from_a_nested_sync_call() -> None:
    """Keep stream work on the invocation loop when sync work runs in a tool thread."""
    source_loop_ids: list[int] = []

    async def events() -> AsyncIterator[StreamEvent]:
        source_loop_ids.append(id(asyncio.get_running_loop()))
        yield StreamEvent(position=1, event_type='status', data={'payload': {}})

    def consume() -> list[StreamEvent]:
        return list(stream_async_iterator(events))

    async def invoke_from_tool_thread() -> int:
        with owning_tool_loop():
            source_loop_id = id(asyncio.get_running_loop())
            streamed = await asyncio.to_thread(consume)
        assert [event.position for event in streamed] == [1]
        return source_loop_id

    source_loop_id = asyncio.run(invoke_from_tool_thread())

    assert source_loop_ids == [source_loop_id]


def test_stream_backpressure_limits_source_advance_while_consumer_pauses() -> None:
    """Do not drain an unbounded number of source events ahead of a sync consumer."""
    fourth_event_pulled = Event()
    event_count = 4

    async def events() -> AsyncIterator[StreamEvent]:
        for position in range(1, event_count + 1):
            if position == event_count:
                fourth_event_pulled.set()
            yield StreamEvent(position=position, event_type='status', data={'payload': {}})

    iterator = stream_async_iterator(events)
    try:
        assert next(iterator).position == 1
        assert not fourth_event_pulled.wait(timeout=_TEST_WAIT_SECONDS)
    finally:
        iterator.close()


def test_explicit_iterator_close_cancels_the_async_source_promptly() -> None:
    """Release a source that would otherwise wait forever after an early sync close."""
    source_closed = Event()

    async def events() -> AsyncIterator[StreamEvent]:
        try:
            yield StreamEvent(position=1, event_type='status', data={'payload': {}})
            await asyncio.Event().wait()
        finally:
            source_closed.set()

    iterator = stream_async_iterator(events)
    assert next(iterator).position == 1

    closer = Thread(target=iterator.close, daemon=True)
    started_at = monotonic()
    closer.start()
    closer.join(timeout=_TEST_WAIT_SECONDS)

    assert not closer.is_alive()
    assert monotonic() - started_at < _TEST_WAIT_SECONDS
    assert source_closed.wait(timeout=_TEST_WAIT_SECONDS)


def test_stream_source_error_is_raised_by_the_sync_iterator() -> None:
    """Preserve a source exception when it crosses the worker-thread boundary."""

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(position=1, event_type='status', data={'payload': {}})
        message = 'source failed'
        raise ValueError(message)

    iterator = stream_async_iterator(events)
    assert next(iterator).position == 1

    with pytest.raises(ValueError, match=r'^source failed$'):
        next(iterator)


def test_stream_source_cancellation_is_raised_by_the_sync_iterator() -> None:
    """Do not leave the sync consumer waiting when an async source is cancelled."""
    consumer_finished = Event()
    caught: list[BaseException] = []

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(position=1, event_type='status', data={'payload': {}})
        raise asyncio.CancelledError

    def consume() -> None:
        try:
            list(stream_async_iterator(events))
        except BaseException as exc:  # noqa: BLE001 - assert cancellation crosses the bridge.
            caught.append(exc)
        finally:
            consumer_finished.set()

    consumer = Thread(target=consume, daemon=True)
    consumer.start()

    assert consumer_finished.wait(timeout=_TEST_WAIT_SECONDS)
    assert caught
    assert isinstance(caught[0], asyncio.CancelledError)


def test_close_while_full_closes_a_retained_source_on_the_owning_tool_loop() -> None:
    """Close a borrowed-loop source before returning even when its queue is full."""
    source_closed = Event()
    third_event_pulled = Event()

    async def events() -> AsyncIterator[StreamEvent]:
        try:
            yield StreamEvent(position=1, event_type='status', data={'payload': {}})
            yield StreamEvent(position=2, event_type='status', data={'payload': {}})
            third_event_pulled.set()
            yield StreamEvent(position=3, event_type='status', data={'payload': {}})
        finally:
            source_closed.set()

    source = events()

    def consume() -> None:
        iterator = stream_async_iterator(lambda: source)
        assert next(iterator).position == 1
        assert third_event_pulled.wait(timeout=_TEST_WAIT_SECONDS)
        iterator.close()
        assert source_closed.is_set()

    async def invoke_from_tool_thread() -> None:
        with owning_tool_loop():
            await asyncio.to_thread(consume)

    asyncio.run(invoke_from_tool_thread())
