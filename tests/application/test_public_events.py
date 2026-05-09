from __future__ import annotations

from maivn import AppEvent, RawSSEEvent, normalize_stream, normalize_stream_event
from maivn.events import (
    ENRICHMENT_EVENT_NAME,
    FINAL_EVENT_NAME,
    INTERRUPT_REQUIRED_EVENT_NAME,
    MODEL_TOOL_COMPLETE_EVENT_NAME,
    STATUS_MESSAGE_EVENT_NAME,
    SYSTEM_TOOL_CHUNK_EVENT_NAME,
    TOOL_EVENT_NAME,
    UPDATE_EVENT_NAME,
)

# MARK: Normalized Stream Helpers


def _names(events: list[AppEvent]) -> list[str]:
    return [event.event_name for event in events]


# MARK: Public Event Normalization


def test_normalize_stream_converts_update_to_assistant_delta() -> None:
    raw_events = [
        RawSSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={"assistant_id": "assistant-1", "streaming_content": "Hello"},
        ),
        RawSSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={"assistant_id": "assistant-1", "streaming_content": "Hello "},
        ),
        RawSSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={"assistant_id": "assistant-1", "streaming_content": "Hello world"},
        ),
    ]

    normalized = list(normalize_stream(raw_events))

    assert _names(normalized) == ["assistant_chunk", "assistant_chunk", "assistant_chunk"]
    assert normalized[0].assistant is not None
    assert normalized[0].assistant.id == "assistant-1"
    assert normalized[0].assistant.delta == "Hello"
    assert normalized[1].assistant is not None
    assert normalized[1].assistant.delta == " "
    assert normalized[2].assistant is not None
    assert normalized[2].assistant.delta == "world"


def test_normalize_stream_preserves_system_tool_chunk_spacing() -> None:
    raw_events = [
        RawSSEEvent(
            name=SYSTEM_TOOL_CHUNK_EVENT_NAME,
            payload={"tool_id": "system-tool-1", "text": "\n  indented output"},
        ),
        RawSSEEvent(
            name=SYSTEM_TOOL_CHUNK_EVENT_NAME,
            payload={"tool_id": "system-tool-1", "text": "   "},
        ),
        RawSSEEvent(
            name=SYSTEM_TOOL_CHUNK_EVENT_NAME,
            payload={"tool_id": "system-tool-1", "text": ""},
        ),
    ]

    normalized = list(normalize_stream(raw_events))

    assert _names(normalized) == ["system_tool_chunk", "system_tool_chunk"]
    assert normalized[0].chunk is not None
    assert normalized[0].chunk.text == "\n  indented output"
    assert normalized[1].chunk is not None
    assert normalized[1].chunk.text == "   "


def test_normalize_stream_converts_tool_and_final_events() -> None:
    raw_events = [
        RawSSEEvent(
            name=TOOL_EVENT_NAME,
            payload={
                "id": "evt-1",
                "value": {
                    "tool_call": {"tool_id": "tool-1", "name": "fetch_data", "args": {"q": "abc"}}
                },
            },
        ),
        RawSSEEvent(
            name=FINAL_EVENT_NAME,
            payload={
                "responses": ["done"],
                "result": {"ok": True},
                "token_usage": {"total_tokens": 3},
            },
        ),
    ]

    normalized = list(normalize_stream(raw_events, default_agent_name="coordinator"))

    assert _names(normalized) == ["tool_event", "final"]
    assert normalized[0].tool is not None
    assert normalized[0].tool.id == "tool-1"
    assert normalized[0].tool.name == "fetch_data"
    assert normalized[0].tool.status == "executing"
    assert normalized[0].tool.args == {"q": "abc"}
    assert normalized[0].scope is not None
    assert normalized[0].scope.type == "agent"
    assert normalized[0].scope.name == "coordinator"
    assert normalized[1].output is not None
    assert normalized[1].output.response == "done"
    assert normalized[1].output.result == {"ok": True}
    assert normalized[1].output.token_usage == {"total_tokens": 3}


def test_normalize_stream_uses_tool_metadata_for_agent_dependencies() -> None:
    raw_events = [
        RawSSEEvent(
            name=TOOL_EVENT_NAME,
            payload={
                "id": "evt-agent-1",
                "value": {
                    "tool_call": {
                        "tool_id": "agent-tool-1",
                        "args": {"prompt": "Analyze the dataset"},
                    }
                },
            },
        )
    ]

    normalized = list(
        normalize_stream(
            raw_events,
            default_agent_name="Research Coordinator",
            default_swarm_name="Research Swarm",
            tool_metadata_map={
                "agent-tool-1": {
                    "tool_name": "Data Analyzer",
                    "tool_type": "agent",
                    "target_agent_id": "agent-2",
                    "swarm_name": "Research Swarm",
                }
            },
        )
    )

    assert _names(normalized) == ["tool_event"]
    assert normalized[0].tool is not None
    assert normalized[0].tool.id == "agent-tool-1"
    assert normalized[0].tool.name == "Data Analyzer"
    assert normalized[0].tool.type == "agent"
    assert normalized[0].tool.args == {"prompt": "Analyze the dataset", "agent_id": "agent-2"}
    dumped = normalized[0].model_dump(mode="json")
    assert dumped["agent_name"] == "Data Analyzer"
    assert dumped["swarm_name"] == "Research Swarm"


def test_normalize_stream_event_uses_assignment_name_map_for_swarm_agents() -> None:
    normalized = normalize_stream_event(
        RawSSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "action_type": "swarm_agent",
                "action_id": "d9bad818-8331-548b-8b2d-06e1d69cb1a4",
                "status": "started",
                "swarm_name": "Research Assistant",
            },
        ),
        assignment_name_map={
            "d9bad818-8331-548b-8b2d-06e1d69cb1a4": "Report Writer",
        },
    )

    assert _names(normalized) == ["agent_assignment"]
    assert normalized[0].assignment is not None
    assert normalized[0].assignment.agent_name == "Report Writer"
    assert normalized[0].assignment.id == "d9bad818-8331-548b-8b2d-06e1d69cb1a4"


def test_normalize_stream_deferred_model_tool_completion_from_final() -> None:
    raw_events = [
        RawSSEEvent(
            name=MODEL_TOOL_COMPLETE_EVENT_NAME,
            payload={
                "tool_name": "ResearchSummary",
                "event_id": "model-1",
                "result": {"summary": "ok"},
            },
        ),
        RawSSEEvent(
            name=FINAL_EVENT_NAME,
            payload={"responses": ["done"], "result": {"summary": "ok"}},
        ),
    ]

    normalized = list(normalize_stream(raw_events))

    assert _names(normalized) == ["tool_event", "tool_event", "final"]
    assert normalized[0].tool is not None
    assert normalized[0].tool.id == "model-1"
    assert normalized[0].tool.type == "model"
    assert normalized[0].tool.status == "executing"
    assert normalized[1].tool is not None
    assert normalized[1].tool.status == "completed"
    assert normalized[1].tool.result == {"summary": "ok"}


def test_normalize_stream_converts_enrichment_status_and_interrupt_events() -> None:
    raw_events = [
        RawSSEEvent(
            name=ENRICHMENT_EVENT_NAME,
            payload={"phase": "planning", "message": "Planning actions..."},
        ),
        RawSSEEvent(
            name=STATUS_MESSAGE_EVENT_NAME,
            payload={"assistant_id": "assistant-1", "message": "Dispatching..."},
        ),
        RawSSEEvent(
            name=INTERRUPT_REQUIRED_EVENT_NAME,
            payload={
                "interrupt_id": "int-1",
                "data_key": "email",
                "prompt": "Enter email",
                "tool_name": "collect_email",
                "input_type": "text",
                "choices": [],
            },
        ),
    ]

    normalized = list(normalize_stream(raw_events))

    assert _names(normalized) == ["enrichment", "status_message", "interrupt_required"]
    assert normalized[0].enrichment is not None
    assert normalized[0].enrichment.phase == "planning"
    assert normalized[0].enrichment.message == "Planning actions..."
    assert normalized[1].assistant is not None
    assert normalized[1].assistant.id == "assistant-1"
    assert normalized[2].interrupt is not None
    assert normalized[2].interrupt.id == "int-1"
    assert normalized[2].interrupt.data_key == "email"
    assert normalized[2].interrupt.tool_name == "collect_email"
