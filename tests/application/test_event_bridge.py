from __future__ import annotations

import asyncio
import json

from maivn import BridgeRegistry, EventBridge, UIEvent


class _BadString:
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
    parsed = json.loads(sse_payload["data"])

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
    parsed = json.loads(sse_payload["data"])

    # Fallback envelope preserves id/type/timestamp so the frontend's
    # Last-Event-ID cursor and dispatcher remain correct.
    assert sse_payload["event"] == "tool_event"
    assert sse_payload["id"] == "evt-2"
    assert parsed["id"] == "evt-2"
    assert parsed["type"] == "tool_event"
    assert parsed["timestamp"] == "2026-03-19T00:00:00+00:00"
    assert parsed["data"]["serialization_error"] is True
    assert "error_class" in parsed["data"]
    assert isinstance(parsed["data"]["message"], str)


def test_event_bridge_replays_only_unseen_history() -> None:
    async def _run() -> list[dict[str, str]]:
        bridge = EventBridge("session-1", heartbeat_interval=0.01)
        await bridge.emit_status_message("assistant-1", "Working")
        first_id = bridge.get_history()[0]["id"]
        await bridge.emit_final("done", {"ok": True})
        return [event async for event in bridge.generate_sse(last_event_id=first_id)]

    events = asyncio.run(_run())

    assert [event["event"] for event in events] == ["final"]
    parsed = json.loads(events[0]["data"])
    assert parsed["type"] == "final"
    assert parsed["data"]["event_name"] == "final"
    assert parsed["data"]["output"]["response"] == "done"


def test_event_bridge_reopen_starts_a_new_turn() -> None:
    async def _run() -> list[dict[str, str]]:
        bridge = EventBridge("session-2", heartbeat_interval=0.01)
        await bridge.emit_final("first")
        bridge.reopen()
        await bridge.emit_status_message("assistant-1", "next")
        await bridge.emit_error("stop")
        return [event async for event in bridge.generate_sse()]

    events = asyncio.run(_run())

    assert [event["event"] for event in events] == ["status_message", "error"]
    first_payload = json.loads(events[0]["data"])
    second_payload = json.loads(events[1]["data"])
    assert first_payload["data"]["event_name"] == "status_message"
    assert second_payload["data"]["event_name"] == "error"


def test_event_bridge_replays_new_turn_when_resume_cursor_is_stale() -> None:
    async def _run() -> list[dict[str, str]]:
        bridge = EventBridge("session-2b", heartbeat_interval=0.001)
        await bridge.emit_final("first")
        stale_cursor = bridge.get_history()[-1]["id"]
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
    first_payload = json.loads(events[0]["data"])
    second_payload = json.loads(events[1]["data"])
    assert first_payload["data"]["message"] == "next"
    assert second_payload["data"]["error"] == "stop"


def test_event_bridge_emits_comment_frame_keepalive_when_idle() -> None:
    async def _run() -> dict[str, str]:
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
    assert frame["comment"].startswith("keepalive ")
    assert "event" not in frame


def test_bridge_registry_replaces_existing_session_bridge() -> None:
    registry = BridgeRegistry()

    first = registry.create("session-4")
    second = registry.create("session-4")

    assert registry.get("session-4") is second
    assert first is not second
    assert first._closed is True

    registry.remove("session-4")

    assert registry.get("session-4") is None
    assert second._closed is True


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
    assert tool_events[0]["data"]["tool_id"] == "tool-start-1"
    assert tool_events[1]["data"]["tool_id"] == "tool-start-1"


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
    assert tool_events[0]["data"]["tool_id"] == "tool-start-1"
    assert tool_events[1]["data"]["tool_id"] == "tool-start-2"


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
    assert tool_events[0]["data"]["tool_id"] == "reporter-tool-start-1"
    assert tool_events[1]["data"]["tool_id"] == "reporter-tool-start-1"
    assert tool_events[2]["data"]["tool_id"] == "reporter-tool-start-1"
    assert tool_events[2]["data"]["tool"]["id"] == "reporter-tool-start-1"


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

    assert assignments[0]["data"]["assignment_id"] == "assignment-1"
    assert assignments[1]["data"]["assignment_id"] == "assignment-1"
    assert enrichments[0]["data"]["scope_id"] == "scope-1"
    assert enrichments[1]["data"]["scope_id"] == "scope-1"


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
    assert tool_events[0]["data"]["event_name"] == "tool_event"
    assert tool_events[0]["data"]["tool_id"] == "raw-tool-1"
    assert tool_events[1]["data"]["tool_id"] == "raw-tool-1"
    assert tool_events[1]["data"]["tool"]["id"] == "raw-tool-1"


def test_event_bridge_typed_tool_helper_skips_generic_normalization() -> None:
    async def _run() -> tuple[int, int]:
        bridge = EventBridge("session-8b")
        tool_id_resolutions = 0
        generic_normalizations = 0

        original_resolve_tool_id = bridge._tool_identity_resolver.resolve_tool_id
        original_normalize_payload = bridge._payload_normalizer.normalize_payload

        def count_resolve_tool_id(*args: object, **kwargs: object) -> str:
            nonlocal tool_id_resolutions
            tool_id_resolutions += 1
            return original_resolve_tool_id(*args, **kwargs)

        def count_normalize_payload(event_type: str, data: dict[str, object]) -> dict[str, object]:
            nonlocal generic_normalizations
            generic_normalizations += 1
            return original_normalize_payload(event_type, data)

        bridge._tool_identity_resolver.resolve_tool_id = count_resolve_tool_id
        bridge._payload_normalizer.normalize_payload = count_normalize_payload

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
        return bridge.get_history()[0]["data"]

    data = asyncio.run(_run())

    assert data["redaction"]["inserted_keys"] == ["pii_email_1"]
    assert data["redaction"]["added_private_data"] == {"pii_email_1": "<redacted>"}
    assert data["redaction"]["merged_private_data"] == {
        "existing": "<redacted>",
        "pii_email_1": "<redacted>",
    }
    assert data["redaction"]["matched_known_pii_values"] == ["<redacted>"]
    assert data["redaction"]["unmatched_known_pii_values"] == ["<redacted>"]
    assert data["enrichment"]["redaction"]["added_private_data"] == {"pii_email_1": "<redacted>"}


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
        return bridge.get_history()[0]["data"]

    data = asyncio.run(_run())

    assert data["result"]["result"] == {"ok": True}
    assert data["result"]["private_data_injected"] == ["foo"]
    assert data["result"]["interrupt_data_injected"] == ["prompt"]
    assert data["tool"]["result"]["private_data_injected"] == ["foo"]
    assert data["tool"]["result"]["interrupt_data_injected"] == ["prompt"]


def test_event_bridge_frontend_safe_sanitizes_error_details() -> None:
    async def _run() -> dict[str, object]:
        bridge = EventBridge("session-safe-error", audience="frontend_safe")
        await bridge.emit_error(
            'File "C:\\\\secret\\\\worker.py" exploded',
            details={"path": "C:\\secret\\worker.py", "code": 500},
        )
        return bridge.get_history()[0]["data"]

    data = asyncio.run(_run())

    assert data["error"] == "An internal error occurred. Please try again."
    assert data["details"] == {}
    assert data["error_info"]["message"] == "An internal error occurred. Please try again."
    assert data["error_info"]["details"] == {}


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
    assert assignments[0]["data"]["assignment_id"] == "assign-1"
    assert assignments[1]["data"]["assignment_id"] == "assign-1"
    assert assignments[1]["data"]["assignment"]["id"] == "assign-1"
    assert enrichments[0]["data"]["scope_id"] == "swarm-1"
    assert enrichments[1]["data"]["scope_id"] == "swarm-1"
    assert enrichments[0]["data"]["event_name"] == "enrichment"


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
