# pyright: strict
from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from maivn_shared import FINAL_EVENT_NAME, UPDATE_EVENT_NAME, HumanMessage, SessionResponse

from maivn._internal.api.agent import Agent
from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.core.interfaces.orchestrator_protocol import (
    AgentOrchestratorInterface,
)
from maivn._internal.utils.reporting.context import current_reporter, get_current_reporter
from maivn._internal.utils.reporting.terminal_reporter.reporters.simple_reporter import (
    SimpleReporter,
)


class _DummyOrchestrator:
    def __init__(self) -> None:
        self.invoke_verbose: bool | None = None
        self.stream_verbose: bool | None = None
        self.stream_close_reporter_present: bool | None = None

    def invoke(self, *args: object, **kwargs: object) -> SessionResponse:
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

    def stream(self, *args: object, **kwargs: object) -> Iterator[SSEEvent]:
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


def _attach_dummy(agent: Agent, dummy: _DummyOrchestrator) -> None:
    """Attach a `_DummyOrchestrator` as the agent's orchestrator via boundary cast.

    `_DummyOrchestrator` only stubs the methods exercised by these tests; the cast
    through `object` is Pattern 2 (third-party stub gap escape hatch). `setattr` is
    used in lieu of direct private-attribute assignment so basedpyright does not flag
    a `reportPrivateUsage` warning on the test's outside-class write.
    """
    setattr(  # noqa: B010 - Pydantic PrivateAttr assignment.
        agent,
        "_orchestrator",
        cast(AgentOrchestratorInterface, cast(object, dummy)),
    )


def test_events_builder_auto_verbose_and_payload_routing() -> None:
    agent = Agent(api_key="test")
    dummy = _DummyOrchestrator()
    _attach_dummy(agent, dummy)

    payloads: list[dict[str, object]] = []
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
    _attach_dummy(agent, dummy)

    payloads: list[dict[str, object]] = []
    base_reporter = SimpleReporter(enabled=False)
    token = current_reporter.set(base_reporter)
    try:
        _ = agent.events(
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
    _attach_dummy(agent, dummy)

    payloads: list[dict[str, object]] = []
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
    final_event = cast(SSEEvent, events[-1])
    assert final_event.name == FINAL_EVENT_NAME
    categories = {entry.get("category") for entry in payloads}
    assert categories == {"enrichment", "agent"}


def test_events_builder_stream_early_close_resets_reporter_context() -> None:
    agent = Agent(api_key="test")
    dummy = _DummyOrchestrator()
    _attach_dummy(agent, dummy)

    payloads: list[dict[str, object]] = []
    base_reporter = SimpleReporter(enabled=False)
    token = current_reporter.set(base_reporter)
    try:
        stream_iter = agent.events(
            include=["enrichment", "agent"], on_event=payloads.append
        ).stream([HumanMessage(content="hello")])
        first_event = cast(SSEEvent, next(stream_iter))
        assert first_event.name == UPDATE_EVENT_NAME
        assert get_current_reporter() is base_reporter
        # `EventInvocationBuilder.stream` returns a generator wrapping the underlying iterator,
        # so `.close()` is always available even though the static type is `Iterator[object]`.
        close_fn = getattr(stream_iter, "close", None)
        assert callable(close_fn)
        _ = close_fn()
    finally:
        current_reporter.reset(token)

    assert dummy.stream_close_reporter_present is True
    categories = {entry.get("category") for entry in payloads}
    assert categories == {"enrichment", "agent"}
