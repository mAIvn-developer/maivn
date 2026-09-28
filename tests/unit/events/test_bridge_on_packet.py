"""Tests for EventBridge's optional ``on_packet`` observer hook.

Added for task-194 S3 (maivn-studio local history) BLOCKER 3: the observer
must fire post-normalization/dedup/validation/security-sanitization and
pre-enqueue, must never fire for events a dedup policy drops, and (when it
returns an awaitable) must be awaited before the event reaches the live
queue - giving a caller a way to acknowledge a durable write before the
browser sees the event. Unset, behavior is byte-identical to before this
hook existed.

No async pytest plugin (pytest-asyncio/anyio) is configured for this
package's test suite, so each test drives its own coroutine via
``asyncio.run()`` from a plain sync test function rather than adding a new
test-only dependency.
"""

from __future__ import annotations

import asyncio

import pytest

from maivn.events import EventBridge, UIEvent


def test_on_packet_unset_is_zero_behavior_change() -> None:
    """No ``on_packet`` supplied: emit() behaves exactly as before the hook."""

    async def _run() -> None:
        bridge = EventBridge('session-1')
        await bridge.emit('status_message', {'assistant_id': 'a1', 'message': 'hi'})

        assert bridge.stream_queue_empty() is False
        event = bridge.stream_queue_get_nowait()
        assert event.type == 'status_message'

    asyncio.run(_run())


def test_on_packet_receives_canonical_post_pipeline_event() -> None:
    """The observer sees the same canonical event object enqueued for SSE."""
    seen: list[UIEvent] = []

    def _observe(event: UIEvent) -> None:
        seen.append(event)

    async def _run() -> None:
        bridge = EventBridge('session-1', on_packet=_observe)
        await bridge.emit('status_message', {'assistant_id': 'a1', 'message': 'hi'})

        assert len(seen) == 1
        assert seen[0].type == 'status_message'
        # The observer sees the same object identity enqueued for SSE - not a
        # pre-normalization/pre-security copy.
        queued = bridge.stream_queue_get_nowait()
        assert seen[0] is queued

    asyncio.run(_run())


def test_on_packet_not_called_for_deduped_interrupt() -> None:
    """A duplicate interrupt the bridge drops never reaches the observer."""
    calls: list[UIEvent] = []

    def _observe(event: UIEvent) -> None:
        calls.append(event)

    async def _run() -> None:
        bridge = EventBridge('session-1', on_packet=_observe, dedupe_interrupts=True)
        await bridge.emit_interrupt_required(
            interrupt_id='i1', data_key='dk', prompt='Confirm?', arg_name='x'
        )
        await bridge.emit_interrupt_required(
            interrupt_id='i2', data_key='dk', prompt='Confirm?', arg_name='x'
        )

        interrupt_calls = [event for event in calls if event.type == 'interrupt_required']
        assert len(interrupt_calls) == 1

    asyncio.run(_run())


def test_awaitable_on_packet_delays_enqueue_until_resolved() -> None:
    """An async observer's whole coroutine runs before enqueue proceeds."""
    order: list[str] = []

    async def _observe(_event: UIEvent) -> None:
        order.append('observer-start')
        await asyncio.sleep(0)
        order.append('observer-done')

    async def _run() -> None:
        bridge = EventBridge('session-1', on_packet=_observe)

        order.append('before-emit')
        await bridge.emit('status_message', {'assistant_id': 'a1', 'message': 'hi'})
        order.append('after-emit')

        # The observer must fully resolve (including the internal await
        # point) before emit() returns - i.e. before the caller can rely on
        # the event having been enqueued for the browser.
        assert order == ['before-emit', 'observer-start', 'observer-done', 'after-emit']
        assert bridge.stream_queue_empty() is False

    asyncio.run(_run())


_OBSERVER_FAILURE_MESSAGE = 'durable write failed'


def test_on_packet_exception_propagates_and_blocks_enqueue() -> None:
    """An observer failure propagates out of emit() rather than being swallowed.

    The SDK hook itself does not catch observer exceptions - a caller that
    wants "history problems never break the live run" must implement that in
    its own observer, matching the rest of this codebase's convention of
    catching at the call site rather than in shared infrastructure.
    """

    def _observe(_event: UIEvent) -> None:
        raise RuntimeError(_OBSERVER_FAILURE_MESSAGE)

    async def _run() -> None:
        bridge = EventBridge('session-1', on_packet=_observe)

        with pytest.raises(RuntimeError, match=_OBSERVER_FAILURE_MESSAGE):
            await bridge.emit('status_message', {'assistant_id': 'a1', 'message': 'hi'})

        # The event never reached the live queue because the observer raised
        # before ``_enqueue_event`` ran.
        assert bridge.stream_queue_empty() is True

    asyncio.run(_run())
