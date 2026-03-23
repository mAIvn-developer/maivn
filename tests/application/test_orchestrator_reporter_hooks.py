from __future__ import annotations

from typing import Any

from maivn._internal.core.orchestrator.events import OrchestratorReporterHooks


class _Reporter:
    def __init__(self) -> None:
        self.model_calls: list[dict[str, Any]] = []
        self.start_calls: list[dict[str, Any]] = []
        self.chunk_calls: list[dict[str, Any]] = []
        self.response_chunk_calls: list[dict[str, Any]] = []
        self.complete_calls: list[dict[str, Any]] = []
        self.error_calls: list[dict[str, Any]] = []
        self.assignment_calls: list[dict[str, Any]] = []
        self.enrichment_calls: list[dict[str, Any]] = []

    def report_model_tool_complete(self, tool_name: str, **kwargs: Any) -> None:
        self.model_calls.append({"tool_name": tool_name, **kwargs})

    def report_tool_start(self, tool_name: str, event_id: str, **kwargs: Any) -> None:
        self.start_calls.append({"tool_name": tool_name, "event_id": event_id, **kwargs})

    def report_system_tool_progress(self, **kwargs: Any) -> None:
        self.chunk_calls.append(kwargs)

    def report_response_chunk(self, text: str, **kwargs: Any) -> None:
        self.response_chunk_calls.append({"text": text, **kwargs})

    def report_tool_complete(self, event_id: str, **kwargs: Any) -> None:
        self.complete_calls.append({"event_id": event_id, **kwargs})

    def report_tool_error(self, tool_name: str, error: str, **kwargs: Any) -> None:
        self.error_calls.append({"tool_name": tool_name, "error": error, **kwargs})

    def report_agent_assignment(self, **kwargs: Any) -> None:
        self.assignment_calls.append(kwargs)

    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, Any] | None = None,
        redaction: dict[str, Any] | None = None,
    ) -> None:
        self.enrichment_calls.append(
            {
                "phase": phase,
                "message": message,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "scope_type": scope_type,
                "memory": memory,
                "redaction": redaction,
            }
        )


class _LegacyEnrichmentReporter:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def report_enrichment(self, *, phase: str, message: str) -> None:
        self.calls.append({"phase": phase, "message": message})


def test_reporter_hooks_model_complete_and_action_update() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(
        reporter_supplier=lambda: reporter,
        tool_agent_lookup=lambda name: "agent" if name == "tool" else None,
        swarm_name_supplier=lambda: "swarm",
    )

    hooks.handle_model_tool_complete(
        {
            "tool_name": "tool",
            "assignment_index": 1,
            "result": {"value": 1},
            "private_data_injected": {"x": "y"},
        }
    )

    assert reporter.model_calls
    call = reporter.model_calls[0]
    assert call["event_id"] == "model-tool:tool:1"
    assert call["agent_name"] == "agent"
    assert call["swarm_name"] == "swarm"
    assert "private_data_injected" in call["result"]

    hooks.handle_action_update(
        {
            "action_type": "swarm_agent",
            "action_name": "alpha",
            "status": "completed",
            "result": {"ok": True},
        }
    )

    assert reporter.assignment_calls
    assert reporter.assignment_calls[0]["status"] == "completed"


def test_reporter_hooks_streaming_content_emits_response_deltas() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_action_update(
        {
            "assistant_id": "orchestrator_agent",
            "streaming_content": "Hello",
        }
    )
    hooks.handle_action_update(
        {
            "assistant_id": "orchestrator_agent",
            "streaming_content": "Hello there",
        }
    )

    assert reporter.response_chunk_calls == [
        {
            "text": "Hello",
            "assistant_id": "orchestrator_agent",
            "full_text": "Hello",
        },
        {
            "text": " there",
            "assistant_id": "orchestrator_agent",
            "full_text": "Hello there",
        },
    ]


def test_reporter_hooks_streaming_content_handles_diverged_snapshots() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_action_update(
        {
            "assistant_id": "agent-1",
            "streaming_content": "INVESTMENT MEMO: TechNova\\",
        }
    )
    hooks.handle_action_update(
        {
            "assistant_id": "agent-1",
            "streaming_content": "INVESTMENT MEMO: TechNova\n\n## EXECUTIVE SUMMARY",
        }
    )

    assert reporter.response_chunk_calls == [
        {
            "text": "INVESTMENT MEMO: TechNova\\",
            "assistant_id": "agent-1",
            "full_text": "INVESTMENT MEMO: TechNova\\",
        },
        {
            "text": "\n\n## EXECUTIVE SUMMARY",
            "assistant_id": "agent-1",
            "full_text": "INVESTMENT MEMO: TechNova\n\n## EXECUTIVE SUMMARY",
        },
    ]


def test_reporter_hooks_nested_streaming_is_suppressed_by_default() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)
    hooks._is_nested = True

    hooks.handle_action_update(
        {
            "assistant_id": "orchestrator_agent",
            "streaming_content": "Hello",
        }
    )

    assert reporter.response_chunk_calls == []


def test_reporter_hooks_nested_streaming_can_be_enabled() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)
    hooks._is_nested = True
    hooks._allow_nested_response_stream = True

    hooks.handle_action_update(
        {
            "assistant_id": "orchestrator_agent",
            "streaming_content": "Hello",
        }
    )

    assert reporter.response_chunk_calls == [
        {
            "text": "Hello",
            "assistant_id": "orchestrator_agent",
            "full_text": "Hello",
        }
    ]


def test_reporter_hooks_system_tools_skip_silent() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_system_tool_start({"tool_name": "reevaluate"})
    hooks.handle_system_tool_chunk({"tool_name": "reevaluate"})
    hooks.handle_system_tool_complete({"tool_name": "reevaluate"})
    hooks.handle_system_tool_error({"tool_name": "reevaluate"})

    assert not reporter.start_calls
    assert not reporter.chunk_calls
    assert not reporter.complete_calls
    assert not reporter.error_calls


def test_reporter_hooks_system_tool_events() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_system_tool_start({"tool_name": "sys", "assignment_index": 2})
    hooks.handle_system_tool_chunk({"tool_name": "sys", "chunk_count": 1, "elapsed_seconds": 0.1})
    hooks.handle_system_tool_complete({"tool_name": "sys", "elapsed_ms": 5, "result": {"ok": True}})
    hooks.handle_system_tool_error({"tool_name": "sys", "error": "oops"})

    assert reporter.start_calls
    assert reporter.start_calls[0]["event_id"].startswith("system-tool:sys")
    assert reporter.chunk_calls
    assert reporter.complete_calls
    assert reporter.complete_calls[0]["result"] == {"ok": True}
    assert reporter.error_calls


def test_reporter_hooks_think_completion_includes_final_result() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_system_tool_complete(
        {
            "tool_name": "think",
            "assignment_index": 7,
            "elapsed_ms": 12,
            "result": {"response": "final internal reasoning summary"},
        }
    )

    assert reporter.complete_calls == [
        {
            "event_id": "system-tool:think:7",
            "elapsed_ms": 12,
            "result": {"response": "final internal reasoning summary"},
        }
    ]


def test_reporter_hooks_enrichment_forwards_scope_and_memory() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_enrichment(
        {
            "phase": "memory_retrieved",
            "message": "Retrieved 3 memory hits",
            "scope_type": "agent",
            "scope_id": "orchestrator_agent",
            "scope_name": "orchestrator_agent",
            "memory": {
                "mode": "retrieve",
                "hit_count": 3,
                "latency_ms": 120,
            },
        }
    )

    assert reporter.enrichment_calls == [
        {
            "phase": "memory_retrieved",
            "message": "Retrieved 3 memory hits",
            "scope_id": "orchestrator_agent",
            "scope_name": "orchestrator_agent",
            "scope_type": "agent",
            "memory": {
                "mode": "retrieve",
                "hit_count": 3,
                "latency_ms": 120,
            },
            "redaction": None,
        }
    ]


def test_reporter_hooks_enrichment_forwards_redaction() -> None:
    reporter = _Reporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_enrichment(
        {
            "phase": "redaction_previewed",
            "message": "Redaction preview completed.",
            "scope_type": "swarm",
            "scope_id": "swarm-1",
            "scope_name": "Research Swarm",
            "redaction": {
                "inserted_keys": ["pii_email_1"],
                "redacted_value_count": 1,
            },
        }
    )

    assert reporter.enrichment_calls == [
        {
            "phase": "redaction_previewed",
            "message": "Redaction preview completed.",
            "scope_id": "swarm-1",
            "scope_name": "Research Swarm",
            "scope_type": "swarm",
            "memory": None,
            "redaction": {
                "inserted_keys": ["pii_email_1"],
                "redacted_value_count": 1,
            },
        }
    ]


def test_reporter_hooks_enrichment_legacy_reporter_ignores_scope_and_memory() -> None:
    reporter = _LegacyEnrichmentReporter()
    hooks = OrchestratorReporterHooks(reporter_supplier=lambda: reporter)

    hooks.handle_enrichment(
        {
            "phase": "memory_indexed",
            "message": "Indexed 4 vectors, 1 graph edges",
            "scope_type": "agent",
            "scope_id": "orchestrator_agent",
            "scope_name": "orchestrator_agent",
            "memory": {"mode": "index", "vector_rows": 4, "graph_edges": 1},
        }
    )

    assert reporter.calls == [
        {
            "phase": "memory_indexed",
            "message": "Indexed 4 vectors, 1 graph edges",
        }
    ]
