"""Concurrency and load tests for ``EventBridge``.

These cover the kinds of failures that only show up under contention:

* High-volume emit from many concurrent producers
* Reconnect mid-stream (Last-Event-ID) while events keep flowing
* Backpressure under a slow consumer
* Cancellation cleanup when the SSE generator is aborted by the framework
* Schema validation guardrails (warn / strict / off)
"""

from __future__ import annotations

import asyncio
import json

import pytest

from maivn.events import EventBridge
from maivn.events._bridge.schema import EventSchemaError, validate_event

# MARK: High-volume concurrency


def test_concurrent_producers_preserve_all_events_in_history() -> None:
    """Many concurrent emits must all land in history with stable ids."""

    async def _run() -> tuple[int, int]:
        bridge = EventBridge("conc-1", max_history=5_000)

        async def producer(producer_id: int, count: int) -> None:
            for i in range(count):
                await bridge.emit(
                    "noise",
                    {"producer": producer_id, "i": i},
                )

        await asyncio.gather(*[producer(p, 200) for p in range(10)])
        history = bridge.get_history()
        unique_ids = {evt["id"] for evt in history}
        return len(history), len(unique_ids)

    total, unique = asyncio.run(_run())
    assert total == 2_000
    assert unique == 2_000  # ids must be unique under contention


def test_reconnect_mid_stream_replays_missing_events_in_order() -> None:
    """Client disconnects after id N, reconnects with last_event_id=N."""

    async def _run() -> tuple[list[str], list[str]]:
        bridge = EventBridge("conc-reconnect", heartbeat_interval=0.001)
        for i in range(10):
            await bridge.emit("evt", {"i": i})

        history = bridge.get_history()
        cursor = history[4]["id"]  # client saw events 0..4

        # Reconnect with cursor; expect events 5..9 (in order).
        seen_ids: list[str] = []
        seen_indices: list[str] = []
        gen = bridge.generate_sse(last_event_id=cursor)
        try:
            async for sse_event in gen:
                if "id" not in sse_event:
                    continue
                payload = json.loads(sse_event["data"])
                seen_ids.append(sse_event["id"])
                seen_indices.append(payload["data"]["i"])
                if payload["data"]["i"] == 9:
                    break
        finally:
            bridge.close()
            await gen.aclose()

        return seen_ids, seen_indices

    ids, indices = asyncio.run(_run())
    assert indices == [5, 6, 7, 8, 9]
    # Ids monotonically progress (no replay of already-seen).
    assert len(ids) == len(set(ids))


# MARK: Backpressure under slow consumer


def test_drop_oldest_under_slow_consumer_keeps_newest() -> None:
    async def _run() -> int:
        bridge = EventBridge(
            "slow-consumer",
            queue_maxsize=5,
            backpressure="drop_oldest",
        )
        for i in range(50):
            await bridge.emit("evt", {"i": i})
        # Queue has at most 5 items; the most recent 5.
        drained: list[int] = []
        while not bridge._queue.empty():
            drained.append(bridge._queue.get_nowait().data["i"])
        assert drained == [45, 46, 47, 48, 49]
        return len(drained)

    assert asyncio.run(_run()) == 5


def test_block_backpressure_pauses_producer_until_consumer_drains() -> None:
    async def _run() -> int:
        bridge = EventBridge(
            "block-bp",
            queue_maxsize=3,
            backpressure="block",
            heartbeat_interval=0.01,
        )

        async def producer() -> None:
            for i in range(10):
                await bridge.emit("evt", {"i": i})

        async def consumer() -> int:
            received = 0
            gen = bridge.generate_sse()
            try:
                async for frame in gen:
                    if "id" not in frame:  # skip keepalives
                        continue
                    received += 1
                    if received == 10:
                        bridge.close()
                        break
                    # Simulate slow consumer.
                    await asyncio.sleep(0.005)
            finally:
                await gen.aclose()
            return received

        producer_task = asyncio.create_task(producer())
        received = await consumer()
        await producer_task
        return received

    assert asyncio.run(_run()) == 10


# MARK: Cancellation cleanup


def test_sse_generator_releases_cleanly_on_aclose_mid_stream() -> None:
    async def _run() -> bool:
        bridge = EventBridge("cancel-clean", heartbeat_interval=0.001)
        gen = bridge.generate_sse()
        # Pull a couple of keepalives so the generator is fully scheduled.
        await asyncio.wait_for(anext(gen), timeout=1.0)
        await asyncio.wait_for(anext(gen), timeout=1.0)
        # Now request cancellation. aclose() raises GeneratorExit inside
        # the generator; the bridge implementation re-raises it per PEP
        # 525 and the awaitable returned by aclose() must complete.
        await gen.aclose()
        return True

    assert asyncio.run(_run()) is True


def test_bridge_survives_cancelled_consumer_and_serves_a_new_one() -> None:
    """A cancelled SSE generator must not corrupt the bridge for the next client."""

    async def _run() -> int:
        bridge = EventBridge("multi-client", heartbeat_interval=0.001)

        first = bridge.generate_sse()
        await asyncio.wait_for(anext(first), timeout=1.0)
        await first.aclose()

        # Emit and consume a real event with a fresh generator.
        await bridge.emit("evt", {"i": 1})
        await bridge.emit_final("done")

        events: list[str] = []
        gen = bridge.generate_sse()
        try:
            async for frame in gen:
                if "id" in frame:
                    events.append(frame["event"])
        finally:
            await gen.aclose()
        return len(events)

    # 1 evt + final terminal
    assert asyncio.run(_run()) == 2


# MARK: Schema validation


def test_validate_event_warn_mode_logs_but_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="maivn.events._bridge.schema"):
        validate_event("tool_event", {"tool_name": "x"}, mode="warn")  # missing tool_id, status

    assert any("missing required fields" in rec.message for rec in caplog.records)


def test_validate_event_strict_mode_raises_for_missing_required_fields() -> None:
    with pytest.raises(EventSchemaError, match="missing required fields"):
        validate_event(
            "interrupt_required",
            {"interrupt_id": "i1"},  # missing data_key, prompt
            mode="strict",
        )


def test_validate_event_off_mode_is_a_noop() -> None:
    # Should not raise even with a clearly invalid payload.
    validate_event("tool_event", {}, mode="off")


def test_validate_event_rejects_reserved_field_names() -> None:
    with pytest.raises(EventSchemaError, match="reserved field names"):
        validate_event(
            "status_message",
            {
                "assistant_id": "x",
                "message": "hi",
                "__class__": "Reserved!",
            },
            mode="strict",
        )


def test_validate_event_passes_through_unknown_event_types() -> None:
    # Unknown types only get the structural check (non-empty type, dict
    # data, no reserved field names) — required-fields are skipped.
    validate_event("custom_event", {"anything": "goes"}, mode="strict")


def test_bridge_strict_validation_blocks_malformed_emit() -> None:
    async def _run() -> None:
        bridge = EventBridge("strict-mode", schema_validation="strict")
        with pytest.raises(EventSchemaError):
            # Missing required `interrupt_id` and `data_key`.
            await bridge.emit_interrupt_required(
                interrupt_id="",  # empty triggers required-field check
                data_key="",
                prompt="anything",
            )

    asyncio.run(_run())


def test_bridge_constructor_rejects_invalid_validation_mode() -> None:
    with pytest.raises(ValueError, match="schema_validation"):
        EventBridge("s", schema_validation="loose")  # type: ignore[arg-type]
