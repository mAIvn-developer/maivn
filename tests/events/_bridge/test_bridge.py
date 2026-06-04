# pyright: strict
from __future__ import annotations

import asyncio
import json
from typing import cast

import pytest
from typing_extensions import override

from maivn import BridgeRegistry, EventBridge, UIEvent


class _BadString:
    @override
    def __str__(self) -> str:
        raise RuntimeError("boom")


def test_ui_event_serializes_to_sse_and_history() -> None:
    event = UIEvent(
        type="status_message",
        data={"message": "Ready"},
        id="evt-1",
        timestamp="2026-03-19T00:00:00+00:00",
    )

    sse_payload = event.to_sse()
    parsed = cast(dict[str, object], json.loads(cast(str, sse_payload["data"])))

    assert sse_payload["event"] == "status_message"
    assert sse_payload["id"] == "evt-1"
    assert parsed == {
        "id": "evt-1",
        "type": "status_message",
        "data": {"message": "Ready"},
        "timestamp": "2026-03-19T00:00:00+00:00",
    }
    assert event.to_dict() == parsed


def test_ui_event_serialization_falls_back_on_payload_errors() -> None:
    event = UIEvent(
        type="tool_event",
        data={"value": _BadString()},
        id="evt-2",
        timestamp="2026-03-19T00:00:00+00:00",
    )

    sse_payload = event.to_sse()
    parsed = cast(dict[str, object], json.loads(cast(str, sse_payload["data"])))

    # Fallback envelope preserves id/type/timestamp so the frontend's
    # Last-Event-ID cursor and dispatcher remain correct.
    assert sse_payload["event"] == "tool_event"
    assert sse_payload["id"] == "evt-2"
    assert parsed["id"] == "evt-2"
    assert parsed["type"] == "tool_event"
    assert parsed["timestamp"] == "2026-03-19T00:00:00+00:00"
    parsed_data = cast(dict[str, object], parsed["data"])
    assert parsed_data["serialization_error"] is True
    assert "error_class" in parsed_data
    assert isinstance(parsed_data["message"], str)


def test_event_bridge_replays_only_unseen_history() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-1", heartbeat_interval=0.01)
        await bridge.emit_status_message("assistant-1", "Working")
        first_id = cast(str, bridge.get_history()[0]["id"])
        await bridge.emit_final("done", {"ok": True})
        return [event async for event in bridge.generate_sse(last_event_id=first_id)]

    events = asyncio.run(_run())

    assert [event["event"] for event in events] == ["final"]
    parsed = cast(dict[str, object], json.loads(cast(str, events[0]["data"])))
    assert parsed["type"] == "final"
    parsed_data = cast(dict[str, object], parsed["data"])
    assert parsed_data["event_name"] == "final"
    assert cast(dict[str, object], parsed_data["output"])["response"] == "done"


def test_event_bridge_resume_at_terminal_event_closes_stream() -> None:
    """Regression (deferred row 103): resuming with ``last_event_id`` pointing at
    the terminal event must close the stream immediately — replay nothing and
    finish, not hang open emitting keepalives forever.
    """

    async def _run() -> tuple[list[dict[str, object]], bool]:
        bridge = EventBridge("session-2c", heartbeat_interval=0.01)
        await bridge.emit_status_message("assistant-1", "Working")
        await bridge.emit_final("done", {"ok": True})
        terminal_id = cast(str, bridge.get_history()[-1]["id"])

        async def _collect() -> list[dict[str, object]]:
            return [event async for event in bridge.generate_sse(last_event_id=terminal_id)]

        # A correctly-closing stream completes well within the timeout; the
        # pre-fix bug left it open emitting keepalives, tripping the timeout.
        events = await asyncio.wait_for(_collect(), timeout=1.0)
        return events, bridge.stream_is_closed

    events, is_closed = asyncio.run(_run())

    assert events == []
    assert is_closed is True


def test_event_bridge_reopen_starts_a_new_turn() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-2", heartbeat_interval=0.01)
        await bridge.emit_final("first")
        bridge.reopen()
        await bridge.emit_status_message("assistant-1", "next")
        await bridge.emit_error("stop")
        return [event async for event in bridge.generate_sse()]

    events = asyncio.run(_run())

    assert [event["event"] for event in events] == ["status_message", "error"]
    first_payload = cast(dict[str, object], json.loads(cast(str, events[0]["data"])))
    second_payload = cast(dict[str, object], json.loads(cast(str, events[1]["data"])))
    assert cast(dict[str, object], first_payload["data"])["event_name"] == "status_message"
    assert cast(dict[str, object], second_payload["data"])["event_name"] == "error"


def test_event_bridge_replays_new_turn_when_resume_cursor_is_stale() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-2b", heartbeat_interval=0.001)
        await bridge.emit_final("first")
        stale_cursor = cast(str, bridge.get_history()[-1]["id"])
        bridge.reopen()
        await bridge.emit_status_message("assistant-1", "next")
        await bridge.emit_error("stop")

        generator = bridge.generate_sse(last_event_id=stale_cursor)
        first = await anext(generator)
        second = await anext(generator)
        await generator.aclose()
        return [first, second]

    events = asyncio.run(_run())

    assert [event["event"] for event in events] == ["status_message", "error"]
    first_payload = cast(dict[str, object], json.loads(cast(str, events[0]["data"])))
    second_payload = cast(dict[str, object], json.loads(cast(str, events[1]["data"])))
    assert cast(dict[str, object], first_payload["data"])["message"] == "next"
    assert cast(dict[str, object], second_payload["data"])["error"] == "stop"


def test_event_bridge_emits_comment_frame_keepalive_when_idle() -> None:
    async def _run() -> dict[str, object]:
        bridge = EventBridge("session-3", heartbeat_interval=0.001)
        generator = bridge.generate_sse()
        keepalive = await anext(generator)
        bridge.close()
        await generator.aclose()
        return keepalive

    frame = asyncio.run(_run())

    # Comment frames carry no event/data; browsers ignore them entirely,
    # so frontends never need to subscribe to or filter a heartbeat type.
    assert "comment" in frame
    assert cast(str, frame["comment"]).startswith("keepalive ")
    assert "event" not in frame


def test_event_bridge_wait_for_subscriber_resolves_when_sse_is_consumed() -> None:
    async def _run() -> bool:
        bridge = EventBridge("session-subscriber", heartbeat_interval=0.001)
        wait_task = asyncio.create_task(bridge.wait_for_subscriber(timeout=0.5))
        generator = bridge.generate_sse()
        _ = await anext(generator)
        result = await wait_task
        await generator.aclose()
        return result

    assert asyncio.run(_run()) is True


def test_event_bridge_wait_for_subscriber_times_out() -> None:
    async def _run() -> bool:
        bridge = EventBridge("session-no-subscriber", heartbeat_interval=0.001)
        return await bridge.wait_for_subscriber(timeout=0.001)

    assert asyncio.run(_run()) is False


def test_event_bridge_wait_for_subscriber_resets_after_disconnect() -> None:
    async def _run() -> bool:
        bridge = EventBridge("session-subscriber-reset", heartbeat_interval=0.001)
        generator = bridge.generate_sse()
        _ = await anext(generator)
        await generator.aclose()

        return await bridge.wait_for_subscriber(timeout=0.001)

    assert asyncio.run(_run()) is False


def test_bridge_registry_replaces_existing_session_bridge() -> None:
    registry = BridgeRegistry()

    first = registry.create("session-4")
    second = registry.create("session-4")

    assert registry.get("session-4") is second
    assert first is not second
    assert first.stream_is_closed is True

    registry.remove("session-4")

    assert registry.get("session-4") is None
    assert second.stream_is_closed is True


def test_bridge_registry_clear_closes_and_empties_every_bridge() -> None:
    registry = BridgeRegistry()

    first = registry.create("session-4a")
    second = registry.create("session-4b")

    assert first.stream_is_closed is False
    assert second.stream_is_closed is False

    registry.clear()

    assert registry.get("session-4a") is None
    assert registry.get("session-4b") is None
    assert first.stream_is_closed is True
    assert second.stream_is_closed is True


def test_bridge_registry_clear_on_empty_registry_is_a_noop() -> None:
    registry = BridgeRegistry()

    registry.clear()

    assert registry.get("session-missing") is None


def test_event_bridge_canonicalizes_tool_ids_for_same_active_instance() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-5")
        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="tool-start-1",
            status="executing",
            args={"route_id": "north"},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )
        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="tool-complete-9",
            status="completed",
            result={"ok": True},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )
        return bridge.get_history()

    history = asyncio.run(_run())

    tool_events = [event for event in history if event["type"] == "tool_event"]
    assert len(tool_events) == 2
    assert cast(dict[str, object], tool_events[0]["data"])["tool_id"] == "tool-start-1"
    assert cast(dict[str, object], tool_events[1]["data"])["tool_id"] == "tool-start-1"


def test_event_bridge_keeps_distinct_active_tool_ids_when_same_tool_runs_twice() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-6")
        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="tool-start-1",
            status="executing",
            args={"route_id": "north"},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )
        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="tool-start-2",
            status="executing",
            args={"route_id": "south"},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )
        return bridge.get_history()

    history = asyncio.run(_run())

    tool_events = [event for event in history if event["type"] == "tool_event"]
    assert len(tool_events) == 2
    assert cast(dict[str, object], tool_events[0]["data"])["tool_id"] == "tool-start-1"
    assert cast(dict[str, object], tool_events[1]["data"])["tool_id"] == "tool-start-2"


def test_event_bridge_coalesces_duplicate_executing_packets_with_partial_metadata() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-6b")
        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="reporter-tool-start-1",
            status="executing",
            args={"route_id": "north"},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )
        await bridge.emit(
            "tool_event",
            {
                "tool": {
                    "id": "normalized-tool-start-2",
                    "name": "fetch_route_data",
                    "type": "func",
                    "status": "executing",
                },
                "agent_name": "Powertrain Optimization Agent",
            },
        )
        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="reporter-tool-complete-9",
            status="completed",
            result={"ok": True},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )
        return bridge.get_history()

    history = asyncio.run(_run())

    tool_events = [event for event in history if event["type"] == "tool_event"]
    assert len(tool_events) == 3
    assert cast(dict[str, object], tool_events[0]["data"])["tool_id"] == "reporter-tool-start-1"
    assert cast(dict[str, object], tool_events[1]["data"])["tool_id"] == "reporter-tool-start-1"
    third_data = cast(dict[str, object], tool_events[2]["data"])
    assert third_data["tool_id"] == "reporter-tool-start-1"
    assert cast(dict[str, object], third_data["tool"])["id"] == "reporter-tool-start-1"


def test_event_bridge_canonicalizes_agent_assignment_and_scope_ids() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-7")
        await bridge.emit_agent_assignment(
            agent_name="Analyzer",
            status="in_progress",
            assignment_id="assignment-1",
            swarm_name="Optimization Swarm",
        )
        await bridge.emit_agent_assignment(
            agent_name="Analyzer",
            status="completed",
            assignment_id="assignment-2",
            swarm_name="Optimization Swarm",
        )
        await bridge.emit_enrichment(
            phase="evaluating",
            message="Evaluating...",
            scope_id="scope-1",
            scope_name="Optimization Swarm",
            scope_type="swarm",
        )
        await bridge.emit_enrichment(
            phase="planning",
            message="Planning...",
            scope_id="scope-2",
            scope_name="Optimization Swarm",
            scope_type="swarm",
        )
        return bridge.get_history()

    history = asyncio.run(_run())

    assignments = [event for event in history if event["type"] == "agent_assignment"]
    enrichments = [event for event in history if event["type"] == "enrichment"]

    assert cast(dict[str, object], assignments[0]["data"])["assignment_id"] == "assignment-1"
    assert cast(dict[str, object], assignments[1]["data"])["assignment_id"] == "assignment-1"
    assert cast(dict[str, object], enrichments[0]["data"])["scope_id"] == "scope-1"
    assert cast(dict[str, object], enrichments[1]["data"])["scope_id"] == "scope-1"


def test_event_bridge_raw_emit_normalizes_known_tool_packets() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-8")
        await bridge.emit(
            "tool_event",
            {
                "tool": {
                    "id": "raw-tool-1",
                    "name": "fetch_route_data",
                    "type": "func",
                    "status": "executing",
                    "args": {"route_id": "north"},
                },
                "agent_name": "Powertrain Optimization Agent",
            },
        )
        await bridge.emit(
            "tool_event",
            {
                "tool": {
                    "id": "raw-tool-99",
                    "name": "fetch_route_data",
                    "type": "func",
                    "status": "completed",
                    "result": {"ok": True},
                },
                "agent_name": "Powertrain Optimization Agent",
            },
        )
        return bridge.get_history()

    history = asyncio.run(_run())

    tool_events = [event for event in history if event["type"] == "tool_event"]
    assert len(tool_events) == 2
    first_data = cast(dict[str, object], tool_events[0]["data"])
    assert first_data["event_name"] == "tool_event"
    assert first_data["tool_id"] == "raw-tool-1"
    second_data = cast(dict[str, object], tool_events[1]["data"])
    assert second_data["tool_id"] == "raw-tool-1"
    assert cast(dict[str, object], second_data["tool"])["id"] == "raw-tool-1"


def test_event_bridge_typed_tool_helper_skips_generic_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maivn.events._bridge.runtime.identity import ToolIdentityResolver
    from maivn.events._bridge.runtime.normalization import BridgePayloadNormalizer

    async def _run() -> tuple[int, int]:
        bridge = EventBridge("session-8b")
        tool_id_resolutions = 0
        generic_normalizations = 0

        # Bridge internals are protected; resolve them via getattr so the
        # type checker only sees public surface, then narrow with cast.
        tool_identity_resolver = cast(
            ToolIdentityResolver,
            getattr(bridge, "_tool_identity_resolver"),  # noqa: B009
        )
        payload_normalizer = cast(
            BridgePayloadNormalizer,
            getattr(bridge, "_payload_normalizer"),  # noqa: B009
        )
        original_resolve_tool_id = tool_identity_resolver.resolve_tool_id
        original_normalize_payload = payload_normalizer.normalize_payload

        def count_resolve_tool_id(
            *,
            tool_name: str,
            tool_id: str,
            status: str,
            args: dict[str, object] | None,
            agent_name: str | None,
            swarm_name: str | None,
            tool_type: str | None,
        ) -> str:
            nonlocal tool_id_resolutions
            tool_id_resolutions += 1
            return original_resolve_tool_id(
                tool_name=tool_name,
                tool_id=tool_id,
                status=status,
                args=args,
                agent_name=agent_name,
                swarm_name=swarm_name,
                tool_type=tool_type,
            )

        def count_normalize_payload(event_type: str, data: dict[str, object]) -> dict[str, object]:
            nonlocal generic_normalizations
            generic_normalizations += 1
            return original_normalize_payload(event_type, data)

        monkeypatch.setattr(tool_identity_resolver, "resolve_tool_id", count_resolve_tool_id)
        monkeypatch.setattr(payload_normalizer, "normalize_payload", count_normalize_payload)

        await bridge.emit_tool_event(
            tool_name="fetch_route_data",
            tool_id="typed-tool-1",
            status="executing",
            args={"route_id": "north"},
            agent_name="Powertrain Optimization Agent",
            tool_type="func",
        )

        return tool_id_resolutions, generic_normalizations

    tool_id_resolutions, generic_normalizations = asyncio.run(_run())

    assert tool_id_resolutions == 1
    assert generic_normalizations == 0


def test_event_bridge_frontend_safe_redacts_sensitive_redaction_fields() -> None:
    async def _run() -> dict[str, object]:
        bridge = EventBridge("session-safe-redaction", audience="frontend_safe")
        await bridge.emit_enrichment(
            phase="redaction_previewed",
            message="Redaction preview completed.",
            redaction={
                "inserted_keys": ["pii_email_1"],
                "added_private_data": {"pii_email_1": "alice@example.com"},
                "merged_private_data": {
                    "existing": "value",
                    "pii_email_1": "alice@example.com",
                },
                "matched_known_pii_values": ["alice@example.com"],
                "unmatched_known_pii_values": ["bob@example.com"],
            },
        )
        return cast(dict[str, object], bridge.get_history()[0]["data"])

    data = asyncio.run(_run())

    redaction = cast(dict[str, object], data["redaction"])
    assert redaction["inserted_keys"] == ["pii_email_1"]
    assert redaction["added_private_data"] == {"pii_email_1": "<redacted>"}
    assert redaction["merged_private_data"] == {
        "existing": "<redacted>",
        "pii_email_1": "<redacted>",
    }
    assert redaction["matched_known_pii_values"] == ["<redacted>"]
    assert redaction["unmatched_known_pii_values"] == ["<redacted>"]
    enrichment = cast(dict[str, object], data["enrichment"])
    enrichment_redaction = cast(dict[str, object], enrichment["redaction"])
    assert enrichment_redaction["added_private_data"] == {"pii_email_1": "<redacted>"}


def test_event_bridge_frontend_safe_summarizes_injected_tool_result_keys() -> None:
    async def _run() -> dict[str, object]:
        bridge = EventBridge("session-safe-tool", audience="frontend_safe")
        await bridge.emit_tool_event(
            tool_name="lookup",
            tool_id="tool-1",
            status="completed",
            tool_type="func",
            result={
                "result": {"ok": True},
                "private_data_injected": {"foo": "bar"},
                "interrupt_data_injected": {"prompt": "yes"},
            },
        )
        return cast(dict[str, object], bridge.get_history()[0]["data"])

    data = asyncio.run(_run())

    result_obj = cast(dict[str, object], data["result"])
    assert result_obj["result"] == {"ok": True}
    assert result_obj["private_data_injected"] == ["foo"]
    assert result_obj["interrupt_data_injected"] == ["prompt"]
    tool_obj = cast(dict[str, object], data["tool"])
    tool_result = cast(dict[str, object], tool_obj["result"])
    assert tool_result["private_data_injected"] == ["foo"]
    assert tool_result["interrupt_data_injected"] == ["prompt"]


def test_event_bridge_frontend_safe_sanitizes_error_details() -> None:
    async def _run() -> dict[str, object]:
        bridge = EventBridge("session-safe-error", audience="frontend_safe")
        await bridge.emit_error(
            'File "C:\\\\secret\\\\worker.py" exploded',
            details={"path": "C:\\secret\\worker.py", "code": 500},
        )
        return cast(dict[str, object], bridge.get_history()[0]["data"])

    data = asyncio.run(_run())

    assert data["error"] == "An internal error occurred. Please try again."
    assert data["details"] == {}
    error_info = cast(dict[str, object], data["error_info"])
    assert error_info["message"] == "An internal error occurred. Please try again."
    assert error_info["details"] == {}


def test_event_bridge_raw_emit_normalizes_assignment_and_enrichment_packets() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-9")
        await bridge.emit(
            "agent_assignment",
            {
                "assignment": {
                    "id": "assign-1",
                    "agent_name": "Analyzer",
                    "status": "in_progress",
                    "swarm_name": "Optimization Swarm",
                }
            },
        )
        await bridge.emit(
            "agent_assignment",
            {
                "assignment": {
                    "id": "assign-2",
                    "agent_name": "Analyzer",
                    "status": "completed",
                    "swarm_name": "Optimization Swarm",
                }
            },
        )
        await bridge.emit(
            "enrichment",
            {
                "enrichment": {
                    "phase": "evaluating",
                    "message": "Evaluating...",
                },
                "scope_id": "swarm-1",
                "scope_name": "Optimization Swarm",
                "scope_type": "swarm",
            },
        )
        await bridge.emit(
            "enrichment",
            {
                "enrichment": {
                    "phase": "planning",
                    "message": "Planning...",
                },
                "scope_id": "swarm-2",
                "scope_name": "Optimization Swarm",
                "scope_type": "swarm",
            },
        )
        return bridge.get_history()

    history = asyncio.run(_run())

    assignments = [event for event in history if event["type"] == "agent_assignment"]
    enrichments = [event for event in history if event["type"] == "enrichment"]
    a0 = cast(dict[str, object], assignments[0]["data"])
    a1 = cast(dict[str, object], assignments[1]["data"])
    assert a0["assignment_id"] == "assign-1"
    assert a1["assignment_id"] == "assign-1"
    assert cast(dict[str, object], a1["assignment"])["id"] == "assign-1"
    e0 = cast(dict[str, object], enrichments[0]["data"])
    e1 = cast(dict[str, object], enrichments[1]["data"])
    assert e0["scope_id"] == "swarm-1"
    assert e1["scope_id"] == "swarm-1"
    assert e0["event_name"] == "enrichment"


def test_event_bridge_raw_emit_preserves_unknown_custom_events() -> None:
    async def _run() -> list[dict[str, object]]:
        bridge = EventBridge("session-10")
        await bridge.emit("custom_event", {"alpha": 1, "nested": {"beta": 2}})
        return bridge.get_history()

    history = asyncio.run(_run())

    assert history == [
        {
            "id": history[0]["id"],
            "type": "custom_event",
            "data": {"alpha": 1, "nested": {"beta": 2}},
            "timestamp": history[0]["timestamp"],
        }
    ]
