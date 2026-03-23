from __future__ import annotations

import asyncio
from typing import Any

from maivn import (
    AppEvent,
    EventBridge,
    NormalizedEventForwardingState,
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_tool_event_payload,
    forward_normalized_stream,
)


class _Reporter:
    def __init__(self) -> None:
        self.session_starts: list[tuple[str, str]] = []
        self.response_chunks: list[dict[str, Any]] = []
        self.status_messages: list[dict[str, Any]] = []
        self.tool_starts: list[dict[str, Any]] = []
        self.system_progress: list[dict[str, Any]] = []
        self.tool_completes: list[dict[str, Any]] = []
        self.tool_errors: list[dict[str, Any]] = []
        self.model_completes: list[dict[str, Any]] = []
        self.assignments: list[dict[str, Any]] = []
        self.enrichments: list[dict[str, Any]] = []
        self.events: list[tuple[str, str]] = []
        self.summaries: list[dict[str, Any] | None] = []
        self.final_responses: list[str] = []
        self.final_results: list[Any] = []

    def report_session_start(self, session_id: str, assistant_id: str) -> None:
        self.session_starts.append((session_id, assistant_id))

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
    ) -> None:
        self.response_chunks.append(
            {
                "text": text,
                "assistant_id": assistant_id,
                "full_text": full_text,
            }
        )

    def report_status_message(self, message: str, *, assistant_id: str | None = None) -> None:
        self.status_messages.append({"message": message, "assistant_id": assistant_id})

    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        self.tool_starts.append(
            {
                "tool_name": tool_name,
                "event_id": event_id,
                "tool_type": tool_type,
                "agent_name": agent_name,
                "tool_args": tool_args,
                "swarm_name": swarm_name,
            }
        )

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        self.system_progress.append(
            {
                "event_id": event_id,
                "tool_name": tool_name,
                "chunk_count": chunk_count,
                "elapsed_seconds": elapsed_seconds,
                "text": text,
            }
        )

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
    ) -> None:
        self.tool_completes.append(
            {
                "event_id": event_id,
                "elapsed_ms": elapsed_ms,
                "result": result,
            }
        )

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        self.tool_errors.append(
            {
                "tool_name": tool_name,
                "error": error,
                "event_id": event_id,
                "elapsed_ms": elapsed_ms,
            }
        )

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: Any | None = None,
    ) -> None:
        self.model_completes.append(
            {
                "tool_name": tool_name,
                "event_id": event_id,
                "agent_name": agent_name,
                "swarm_name": swarm_name,
                "result": result,
            }
        )

    def report_agent_assignment(self, **kwargs: Any) -> None:
        self.assignments.append(kwargs)

    def report_enrichment(self, **kwargs: Any) -> None:
        self.enrichments.append(kwargs)

    def print_event(
        self, event_type: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        _ = details
        self.events.append((event_type, message))

    def print_summary(self, token_usage: dict[str, Any] | None = None) -> None:
        self.summaries.append(token_usage)

    def print_final_response(self, response: str) -> None:
        self.final_responses.append(response)

    def print_final_result(self, result: Any) -> None:
        self.final_results.append(result)


def _event(payload: dict[str, Any]) -> AppEvent:
    return AppEvent.model_validate(payload)


def test_forward_normalized_stream_replays_reporter_semantics() -> None:
    reporter = _Reporter()
    state = NormalizedEventForwardingState()
    events = [
        _event(build_session_start_payload(session_id="sess-1", assistant_id="assistant-root")),
        _event(build_assistant_chunk_payload(assistant_id="assistant-root", text="Hello")),
        _event(build_assistant_chunk_payload(assistant_id="assistant-root", text=" world")),
        _event(
            build_status_message_payload(assistant_id="assistant-root", message="Dispatching...")
        ),
        _event(
            build_tool_event_payload(
                tool_name="search",
                tool_id="sys-1",
                status="executing",
                tool_type="system",
            )
        ),
        _event(build_system_tool_chunk_payload(tool_id="sys-1", text="chunk-1")),
        _event(build_system_tool_chunk_payload(tool_id="sys-1", text="chunk-2")),
        _event(
            build_tool_event_payload(
                tool_name="search",
                tool_id="sys-1",
                status="completed",
                tool_type="system",
                result={"hits": 1},
            )
        ),
        _event(
            build_tool_event_payload(
                tool_name="Classifier",
                tool_id="model-1",
                status="executing",
                tool_type="model",
            )
        ),
        _event(
            build_tool_event_payload(
                tool_name="Classifier",
                tool_id="model-1",
                status="completed",
                tool_type="model",
                result={"label": "ok"},
            )
        ),
        _event(
            build_agent_assignment_payload(
                agent_name="alpha",
                status="completed",
                assignment_id="agent-1",
                swarm_name="research",
                result={"ok": True},
            )
        ),
        _event(
            build_enrichment_payload(
                phase="planning",
                message="Planning actions...",
                scope_type="agent",
                scope_name="coordinator",
                memory={"retrieved_count": 2},
            )
        ),
        _event(
            build_final_payload(
                response="Hello world",
                result={"ok": True},
                token_usage={"total_tokens": 3},
            )
        ),
    ]

    asyncio.run(forward_normalized_stream(events, reporter=reporter, state=state))

    assert reporter.session_starts == [("sess-1", "assistant-root")]
    assert reporter.response_chunks == [
        {
            "text": "Hello",
            "assistant_id": "assistant-root",
            "full_text": "Hello",
        },
        {
            "text": " world",
            "assistant_id": "assistant-root",
            "full_text": "Hello world",
        },
    ]
    assert reporter.status_messages == [
        {"message": "Dispatching...", "assistant_id": "assistant-root"}
    ]
    assert reporter.tool_starts == [
        {
            "tool_name": "search",
            "event_id": "sys-1",
            "tool_type": "system",
            "agent_name": None,
            "tool_args": {},
            "swarm_name": None,
        }
    ]
    assert reporter.system_progress == [
        {
            "event_id": "sys-1",
            "tool_name": "search",
            "chunk_count": 1,
            "elapsed_seconds": 0.0,
            "text": "chunk-1",
        },
        {
            "event_id": "sys-1",
            "tool_name": "search",
            "chunk_count": 2,
            "elapsed_seconds": 0.0,
            "text": "chunk-2",
        },
    ]
    assert reporter.tool_completes == [
        {"event_id": "sys-1", "elapsed_ms": None, "result": {"hits": 1}}
    ]
    assert reporter.model_completes == [
        {
            "tool_name": "Classifier",
            "event_id": "model-1",
            "agent_name": None,
            "swarm_name": None,
            "result": {"label": "ok"},
        }
    ]
    assert reporter.assignments == [
        {
            "agent_name": "alpha",
            "status": "completed",
            "assignment_id": "agent-1",
            "swarm_name": "research",
            "error": None,
            "result": {"ok": True},
        }
    ]
    assert reporter.enrichments == [
        {
            "phase": "planning",
            "message": "Planning actions...",
            "scope_id": None,
            "scope_name": "coordinator",
            "scope_type": "agent",
            "memory": {"retrieved_count": 2},
        }
    ]
    assert reporter.events[-1] == ("success", "Agent execution completed successfully!")
    assert reporter.summaries == [{"total_tokens": 3}]
    assert reporter.final_responses == ["Hello world"]
    assert reporter.final_results == [{"ok": True}]
    assert state.assistant_text_by_id == {"assistant-root": "Hello world"}


def test_forward_normalized_stream_replays_bridge_semantics() -> None:
    bridge = EventBridge("session-bridge")
    events = [
        _event(build_session_start_payload(session_id="sess-2", assistant_id="assistant-root")),
        _event(build_assistant_chunk_payload(assistant_id="assistant-root", text="Hi")),
        _event(
            build_tool_event_payload(
                tool_name="lookup",
                tool_id="tool-1",
                status="executing",
                tool_type="func",
            )
        ),
        _event(
            build_tool_event_payload(
                tool_name="lookup",
                tool_id="tool-1",
                status="completed",
                tool_type="func",
                result={"ok": True},
            )
        ),
        _event(
            build_tool_event_payload(
                tool_name="think",
                tool_id="sys-2",
                status="executing",
                tool_type="system",
            )
        ),
        _event(build_system_tool_chunk_payload(tool_id="sys-2", text="pondering")),
        _event(
            build_tool_event_payload(
                tool_name="think",
                tool_id="sys-2",
                status="completed",
                tool_type="system",
                result={"answer": 42},
            )
        ),
        _event(
            build_tool_event_payload(
                tool_name="Summarize",
                tool_id="model-2",
                status="executing",
                tool_type="model",
            )
        ),
        _event(
            build_tool_event_payload(
                tool_name="Summarize",
                tool_id="model-2",
                status="completed",
                tool_type="model",
                result={"summary": "ok"},
            )
        ),
        _event(
            build_interrupt_required_payload(
                interrupt_id="int-1",
                data_key="email",
                prompt="Enter email",
                tool_name="collect_email",
            )
        ),
        _event(build_enrichment_payload(phase="planning", message="Planning actions...")),
        _event(
            build_final_payload(
                response="done",
                result={"ok": True},
                token_usage={"total_tokens": 7},
            )
        ),
    ]

    asyncio.run(forward_normalized_stream(events, bridge=bridge))

    history = bridge.get_history()
    assert [item["type"] for item in history] == [
        "session_start",
        "assistant_chunk",
        "tool_event",
        "tool_event",
        "system_tool_start",
        "system_tool_chunk",
        "system_tool_complete",
        "tool_event",
        "interrupt_required",
        "enrichment",
        "final",
    ]
    assert history[0]["data"]["session"]["id"] == "sess-2"
    assert history[4]["data"]["event_name"] == "system_tool_start"
    assert history[6]["data"]["event_name"] == "system_tool_complete"
    assert history[7]["data"]["tool"]["type"] == "model"
    assert history[7]["data"]["tool"]["status"] == "completed"
    assert history[8]["data"]["interrupt"]["id"] == "int-1"
    assert history[-1]["data"]["output"]["token_usage"] == {"total_tokens": 7}
