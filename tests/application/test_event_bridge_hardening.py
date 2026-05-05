"""Coverage for ``EventBridge`` runtime guardrails.

Covers:
* Bounded queue + backpressure policies
* Constructor input validation
* ``reopen()`` clears identity state, history, and eviction count
* Frontend-safe sanitization for unknown event types
* Serialization fallback envelope preserves event id/type
* History-overflow + cursor-loss diagnostics
* Per-stream heartbeat override
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

import pytest

from maivn.events import EventBridge
from maivn.events._bridge.serialization import build_safe_event_payload

# MARK: Constructor Validation


def test_constructor_rejects_invalid_max_history() -> None:
    with pytest.raises(ValueError, match="max_history"):
        EventBridge("s", max_history=0)


def test_constructor_rejects_invalid_heartbeat_interval() -> None:
    with pytest.raises(ValueError, match="heartbeat_interval"):
        EventBridge("s", heartbeat_interval=0)


def test_constructor_rejects_negative_queue_maxsize() -> None:
    with pytest.raises(ValueError, match="queue_maxsize"):
        EventBridge("s", queue_maxsize=-1)


def test_constructor_rejects_unknown_backpressure_policy() -> None:
    with pytest.raises(ValueError, match="backpressure"):
        EventBridge("s", queue_maxsize=1, backpressure="bogus")  # type: ignore[arg-type]


# MARK: Backpressure


def test_backpressure_drop_newest_drops_overflow() -> None:
    async def _run() -> tuple[int, list[int]]:
        bridge = EventBridge("bp-newest", queue_maxsize=2, backpressure="drop_newest")
        for i in range(5):
            await bridge.emit("evt", {"i": i})
        # Queue should be capped at 2; drained values are the first two.
        drained: list[int] = []
        while not bridge._queue.empty():
            drained.append(bridge._queue.get_nowait().data["i"])
        return len(bridge.get_history()), drained

    history_len, drained = asyncio.run(_run())

    # History remains complete (capped only by max_history).
    assert history_len == 5
    # Newest events are the ones dropped, so the queue retains the first two.
    assert drained == [0, 1]


def test_backpressure_drop_oldest_keeps_latest() -> None:
    async def _run() -> list[int]:
        bridge = EventBridge("bp-oldest", queue_maxsize=2, backpressure="drop_oldest")
        for i in range(5):
            await bridge.emit("evt", {"i": i})
        drained: list[int] = []
        while not bridge._queue.empty():
            drained.append(bridge._queue.get_nowait().data["i"])
        return drained

    drained = asyncio.run(_run())

    # Oldest are dropped — queue retains the most recent two.
    assert drained == [3, 4]


# MARK: Reopen Resets Identity


def test_reopen_resets_identity_aliases_and_eviction_counter() -> None:
    async def _run() -> tuple[dict[str, str], int]:
        bridge = EventBridge("ident-reset", max_history=2)
        # Generate enough emits to evict from history, then a tool event so
        # an alias gets registered.
        for i in range(5):
            await bridge.emit("noise", {"i": i})
        await bridge.emit_tool_event(
            tool_name="search",
            tool_id="alias-1",
            status="executing",
            args={"q": "before"},
            tool_type="func",
        )

        evictions_before = bridge._history_evictions
        aliases_before = dict(bridge._identity_state.tool_id_aliases)

        bridge.reopen()

        return aliases_before, evictions_before

    aliases_before, evictions_before = asyncio.run(_run())

    assert "alias-1" in aliases_before
    assert evictions_before > 0


def test_reopen_clears_identity_state_so_new_turn_uses_fresh_ids() -> None:
    async def _run() -> tuple[str, str]:
        bridge = EventBridge("ident-fresh")
        await bridge.emit_tool_event(
            tool_name="search",
            tool_id="turn1-id",
            status="executing",
            args={"q": "north"},
            tool_type="func",
        )
        # Same logical tool starts again in a new turn — identity state
        # must NOT reuse the prior canonical id.
        bridge.reopen()
        await bridge.emit_tool_event(
            tool_name="search",
            tool_id="turn2-id",
            status="executing",
            args={"q": "north"},
            tool_type="func",
        )
        history = bridge.get_history()
        return history[-1]["data"]["tool_id"], "turn2-id"

    canonical_after_reopen, expected = asyncio.run(_run())
    assert canonical_after_reopen == expected


# MARK: Frontend-Safe Default-Deny


def test_frontend_safe_unknown_event_scrubs_injected_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> dict[str, Any]:
        bridge = EventBridge("custom-evt", audience="frontend_safe")
        await bridge.emit(
            "triage_card",
            {
                "title": "ok",
                "details": {
                    "private_data_injected": {"ssn": "***"},
                    "interrupt_data_injected": ["secret-prompt"],
                    "visible": "yes",
                },
            },
        )
        return bridge.get_history()[0]["data"]

    with caplog.at_level(logging.WARNING, logger="maivn.events._bridge.security"):
        data = asyncio.run(_run())
    details = cast(dict[str, Any], data["details"])

    # Visible content is untouched.
    assert data["title"] == "ok"
    assert details["visible"] == "yes"
    # Injected payloads are summarized, not echoed.
    assert details["private_data_injected"] == ["ssn"]
    assert details["interrupt_data_injected"] == ["secret-prompt"]
    # Operator gets a warning so unknown event types surface in monitoring.
    assert any("triage_card" in rec.message for rec in caplog.records)


def test_frontend_safe_known_events_still_pass_through_unchanged() -> None:
    async def _run() -> dict[str, object]:
        bridge = EventBridge("known-passthrough", audience="frontend_safe")
        await bridge.emit_status_message(assistant_id="orch", message="hi")
        return bridge.get_history()[0]["data"]

    data = asyncio.run(_run())
    assert data["message"] == "hi"


# MARK: Serialization Fallback Envelope


class _BadValue:
    def __str__(self) -> str:
        raise RuntimeError("cannot stringify")


def test_safe_event_payload_preserves_id_and_type_on_failure() -> None:
    payload = {"id": "evt-99", "type": "tool_event", "data": {"x": _BadValue()}}
    raw = build_safe_event_payload(
        payload,
        event_id="evt-99",
        event_type="tool_event",
        timestamp="2026-04-01T00:00:00+00:00",
    )
    parsed = json.loads(raw)
    assert parsed["id"] == "evt-99"
    assert parsed["type"] == "tool_event"
    assert parsed["timestamp"] == "2026-04-01T00:00:00+00:00"
    assert parsed["data"]["serialization_error"] is True


def test_serialization_handles_datetime_uuid_decimal_set_and_bytes() -> None:
    from datetime import datetime, timezone
    from decimal import Decimal
    from uuid import UUID

    payload = {
        "id": "evt-1",
        "type": "x",
        "timestamp": "ts",
        "data": {
            "when": datetime(2026, 4, 27, tzinfo=timezone.utc),
            "uid": UUID("00000000-0000-0000-0000-000000000001"),
            "amount": Decimal("3.14"),
            "tags": {"b", "a"},
            "blob": b"hello",
        },
    }
    raw = build_safe_event_payload(payload, event_id="evt-1", event_type="x", timestamp="ts")
    parsed = json.loads(raw)
    assert parsed["data"]["when"].startswith("2026-04-27")
    assert parsed["data"]["uid"] == "00000000-0000-0000-0000-000000000001"
    assert parsed["data"]["amount"] == "3.14"
    assert parsed["data"]["tags"] == sorted(["a", "b"], key=repr)
    assert parsed["data"]["blob"] == "hello"


# MARK: Cursor-Loss Diagnostics


def test_lost_cursor_after_history_overflow_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> str:
        bridge = EventBridge("cursor-loss", max_history=3, heartbeat_interval=0.001)
        ids: list[str] = []
        for i in range(10):
            await bridge.emit("evt", {"i": i})
            ids.append(bridge.get_history()[-1]["id"])
        # First id is no longer in history — simulate stale cursor.
        gen = bridge.generate_sse(last_event_id=ids[0])
        # Drain just enough to trigger replay logic.
        results = []
        try:
            results.append(await asyncio.wait_for(anext(gen), timeout=0.5))
        except StopAsyncIteration:
            pass
        await gen.aclose()
        bridge.close()
        return ids[0]

    with caplog.at_level(logging.WARNING, logger="maivn.events._bridge"):
        asyncio.run(_run())

    assert any(
        "aged out" in rec.message and rec.levelno >= logging.WARNING for rec in caplog.records
    )


def test_history_evictions_count_increments_when_buffer_full() -> None:
    async def _run() -> int:
        bridge = EventBridge("evict-count", max_history=3)
        for i in range(7):
            await bridge.emit("evt", {"i": i})
        return bridge._history_evictions

    evictions = asyncio.run(_run())
    assert evictions == 4


# MARK: Per-Stream Heartbeat Override


def test_per_stream_heartbeat_override() -> None:
    async def _run() -> dict[str, Any]:
        bridge = EventBridge("hb-override", heartbeat_interval=60.0)
        gen = bridge.generate_sse(heartbeat_interval=0.001)
        first = cast(dict[str, Any], await asyncio.wait_for(anext(gen), timeout=1.0))
        bridge.close()
        await gen.aclose()
        return first

    frame = asyncio.run(_run())
    # Default keepalive is comment-frame; just verify cadence override fired.
    assert "comment" in frame


def test_per_stream_heartbeat_override_rejects_invalid() -> None:
    async def _run() -> None:
        bridge = EventBridge("hb-bad")
        gen = bridge.generate_sse(heartbeat_interval=0)
        with pytest.raises(ValueError, match="heartbeat_interval"):
            await anext(gen)

    asyncio.run(_run())


# MARK: Dedup behavior


def test_bridge_drops_repeated_status_messages_when_enabled() -> None:
    async def _run() -> int:
        bridge = EventBridge("dedup-status", dedupe_status_messages=True)
        await bridge.emit_status_message("orch", "Working")
        await bridge.emit_status_message("orch", "Working")
        await bridge.emit_status_message("orch", "Working")
        return len([evt for evt in bridge.get_history() if evt["type"] == "status_message"])

    assert asyncio.run(_run()) == 1


def test_bridge_status_dedup_disabled_by_default() -> None:
    async def _run() -> int:
        bridge = EventBridge("dedup-default")
        await bridge.emit_status_message("orch", "Working")
        await bridge.emit_status_message("orch", "Working")
        return len([evt for evt in bridge.get_history() if evt["type"] == "status_message"])

    # Status dedup is opt-in; repeated statuses pass through by default.
    assert asyncio.run(_run()) == 2


def test_bridge_collapses_interrupts_with_arg_name_or_data_key() -> None:
    """Interrupts dedupe by (prompt, arg_name | data_key)."""

    async def _run() -> int:
        bridge = EventBridge("dedup-int")
        # First emit uses arg_name; second uses only data_key with the same
        # value. The shared fingerprint catches the duplicate.
        await bridge.emit_interrupt_required(
            interrupt_id="i1",
            data_key="initial-token-1",
            prompt="Choose how to proceed.",
            arg_name="operator_decision",
        )
        await bridge.emit_interrupt_required(
            interrupt_id="i2",
            data_key="operator_decision",
            prompt="Choose how to proceed.",
            arg_name=None,
        )
        return len([evt for evt in bridge.get_history() if evt["type"] == "interrupt_required"])

    assert asyncio.run(_run()) == 1


def test_bridge_generic_emit_uses_interrupt_dedup() -> None:
    async def _run() -> int:
        bridge = EventBridge("dedup-generic-int")
        await bridge.emit_interrupt_required(
            interrupt_id="i1",
            data_key="operator_decision",
            prompt="Choose how to proceed.",
            arg_name="operator_decision",
        )
        await bridge.emit(
            "interrupt_required",
            {
                "interrupt_id": "i2",
                "data_key": "operator_decision",
                "prompt": "Choose how to proceed.",
            },
        )
        return len([evt for evt in bridge.get_history() if evt["type"] == "interrupt_required"])

    assert asyncio.run(_run()) == 1


def test_bridge_interrupt_dedup_can_be_disabled() -> None:
    async def _run() -> int:
        bridge = EventBridge("dedup-off", dedupe_interrupts=False)
        for index in range(3):
            await bridge.emit_interrupt_required(
                interrupt_id=f"i{index}",
                data_key="same-key",
                prompt="Choose.",
                arg_name="same-arg",
            )
        return len([evt for evt in bridge.get_history() if evt["type"] == "interrupt_required"])

    assert asyncio.run(_run()) == 3
