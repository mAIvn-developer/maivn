from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from maivn_shared import FINAL_EVENT_NAME, UPDATE_EVENT_NAME, HumanMessage, SessionResponse

from maivn._internal.api.agent import Agent
from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.utils.reporting.context import current_reporter, get_current_reporter
from maivn._internal.utils.reporting.terminal_reporter.reporters.simple_reporter import (
    SimpleReporter,
)


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.invoke_verbose: bool | None = None
        self.stream_verbose: bool | None = None
        self.stream_close_reporter_present: bool | None = None

    def invoke(self, *args: Any, **kwargs: Any) -> SessionResponse:
        _ = args
        self.invoke_verbose = bool(kwargs.get("verbose"))
        reporter = get_current_reporter()
        assert reporter is not None

        reporter.report_enrichment(phase="planning", message="Planning actions...")
        reporter.report_tool_start(
            "fetch_data",
            "tool-1",
            "func",
            "coordinator",
            {"arg_keys": ["source"]},
            "research_swarm",
        )
        reporter.report_tool_complete("tool-1", elapsed_ms=12, result={"ok": True})
        reporter.report_model_tool_complete(
            "ResearchSummary",
            event_id="model-1",
            agent_name="coordinator",
            swarm_name="research_swarm",
            result={"summary": "ok"},
        )
        return SessionResponse(responses=["done"])

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[SSEEvent]:
        _ = args
        self.stream_verbose = bool(kwargs.get("verbose"))
        reporter = get_current_reporter()
        assert reporter is not None

        try:
            reporter.report_enrichment(phase="evaluating", message="Evaluating request...")
            reporter.report_tool_start(
                "delegate_agent",
                "tool-2",
                "agent",
                "coordinator",
                {"arg_keys": ["prompt"]},
                "research_swarm",
            )
            reporter.report_tool_complete("tool-2", elapsed_ms=9, result={"response": "ok"})
            yield SSEEvent(name=UPDATE_EVENT_NAME, payload={"step": 1})
            yield SSEEvent(
                name=FINAL_EVENT_NAME, payload={"status": "completed", "responses": ["done"]}
            )
        finally:
            self.stream_close_reporter_present = get_current_reporter() is not None


def test_events_builder_auto_verbose_and_payload_routing() -> None:
    agent = Agent(api_key="test")
    dummy = _DummyOrchestrator()
    agent._orchestrator = dummy  # type: ignore[assignment]

    payloads: list[dict[str, Any]] = []
    base_reporter = SimpleReporter(enabled=False)
    token = current_reporter.set(base_reporter)
    try:
        response = agent.events(on_event=payloads.append).invoke([HumanMessage(content="hello")])
    finally:
        current_reporter.reset(token)

    assert response.responses == ["done"]
    assert dummy.invoke_verbose is True
    categories = {entry.get("category") for entry in payloads}
    assert "enrichment" in categories
    assert "func" in categories
    assert "model" in categories


def test_events_builder_include_exclude_filters() -> None:
    agent = Agent(api_key="test")
    dummy = _DummyOrchestrator()
    agent._orchestrator = dummy  # type: ignore[assignment]

    payloads: list[dict[str, Any]] = []
    base_reporter = SimpleReporter(enabled=False)
    token = current_reporter.set(base_reporter)
    try:
        agent.events(
            include=["enrichment", "model", "func"],
            exclude=["func"],
            on_event=payloads.append,
        ).invoke([HumanMessage(content="hello")])
    finally:
        current_reporter.reset(token)

    categories = {entry.get("category") for entry in payloads}
    assert categories == {"enrichment", "model"}


def test_events_builder_stream_auto_verbose() -> None:
    agent = Agent(api_key="test")
    dummy = _DummyOrchestrator()
    agent._orchestrator = dummy  # type: ignore[assignment]

    payloads: list[dict[str, Any]] = []
    base_reporter = SimpleReporter(enabled=False)
    token = current_reporter.set(base_reporter)
    try:
        events = list(
            agent.events(include=["enrichment", "agent"], on_event=payloads.append).stream(
                [HumanMessage(content="hello")]
            )
        )
    finally:
        current_reporter.reset(token)

    assert dummy.stream_verbose is True
    assert events[-1].name == FINAL_EVENT_NAME
    categories = {entry.get("category") for entry in payloads}
    assert categories == {"enrichment", "agent"}


def test_events_builder_stream_early_close_resets_reporter_context() -> None:
    agent = Agent(api_key="test")
    dummy = _DummyOrchestrator()
    agent._orchestrator = dummy  # type: ignore[assignment]

    payloads: list[dict[str, Any]] = []
    base_reporter = SimpleReporter(enabled=False)
    token = current_reporter.set(base_reporter)
    try:
        stream_iter = agent.events(
            include=["enrichment", "agent"], on_event=payloads.append
        ).stream([HumanMessage(content="hello")])
        first_event = next(stream_iter)
        assert first_event.name == UPDATE_EVENT_NAME
        assert get_current_reporter() is base_reporter
        stream_iter.close()
    finally:
        current_reporter.reset(token)

    assert dummy.stream_close_reporter_present is True
    categories = {entry.get("category") for entry in payloads}
    assert categories == {"enrichment", "agent"}
