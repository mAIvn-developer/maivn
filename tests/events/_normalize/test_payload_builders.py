# pyright: strict
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from maivn import (
    APP_EVENT_CONTRACT_VERSION,
    build_agent_assignment_payload,
    build_enrichment_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
)

# JsonObject mirrors the alias in src/maivn/_internal/utils/reporting/app_event_payloads/common.py.
JsonObject = dict[str, JsonValue]


def _nested(payload: JsonObject, key: str) -> JsonObject:
    """Narrow ``payload[key]`` to a ``JsonObject`` for typed indexing in tests."""
    return cast(JsonObject, payload[key])


def test_build_tool_event_payload_sets_scope_and_participant() -> None:
    payload = build_tool_event_payload(
        tool_name="fetch_data",
        tool_id="tool-1",
        status="executing",
        args={"query": "abc"},
        agent_name="coordinator",
        participant_key="assistant",
        participant_name="Coordinator",
        participant_role="assistant",
    )

    assert payload["contract_version"] == APP_EVENT_CONTRACT_VERSION
    assert payload["event_name"] == "tool_event"
    assert payload["scope"] == {"type": "agent", "name": "coordinator"}
    assert payload["participant"] == {
        "key": "assistant",
        "name": "Coordinator",
        "role": "assistant",
    }
    assert _nested(payload, "tool")["args"] == {"query": "abc"}


def test_build_agent_assignment_payload_normalizes_swarm_scope() -> None:
    payload = build_agent_assignment_payload(
        agent_name="planner",
        status="completed",
        swarm_name="research-swarm",
        result={"ok": True},
    )

    assert payload["event_name"] == "agent_assignment"
    assert payload["scope"] == {"type": "swarm", "name": "research-swarm"}
    assert _nested(payload, "assignment")["result"] == {"ok": True}


def test_build_enrichment_and_system_tool_start_payloads_include_nested_fields() -> None:
    enrichment = build_enrichment_payload(
        phase="planning",
        message="Planning actions...",
        scope_id="scope-1",
        scope_name="Planner",
        scope_type="agent",
        memory={"enabled": True},
        redaction={"mode": "strict"},
    )
    system_tool = build_system_tool_start_payload(
        tool_type="search",
        tool_id="sys-1",
        params={"q": "weather"},
        swarm_name="research-swarm",
    )

    assert enrichment["scope"] == {"id": "scope-1", "name": "Planner", "type": "agent"}
    assert _nested(enrichment, "enrichment")["memory"] == {"enabled": True}
    assert _nested(enrichment, "enrichment")["redaction"] == {"mode": "strict"}
    assert system_tool["scope"] == {"type": "swarm", "name": "research-swarm"}
    assert _nested(system_tool, "tool")["type"] == "system"
    assert _nested(system_tool, "tool")["args"] == {"q": "weather"}


def test_build_enrichment_payload_elides_empty_metric_dicts() -> None:
    """Empty metric dicts (``{}``) mean "no data for this phase" — keys elided.

    Matches the contract exercised by
    ``apps/maivn-studio/tests/test_event_bridge_extended.py::test_emit_enrichment_ignores_empty_dicts``.
    Non-empty dicts (e.g. ``{"recalled": 5}``) ARE preserved; see the
    surrounding tests for that path.
    """
    payload = build_enrichment_payload(
        phase="planning",
        message="Planning actions...",
        memory={},
        redaction={},
        reevaluate={},
    )

    assert "memory" not in payload
    assert "redaction" not in payload
    assert "reevaluate" not in payload
    enrichment = _nested(payload, "enrichment")
    assert "memory" not in enrichment
    assert "redaction" not in enrichment
    assert "reevaluate" not in enrichment
