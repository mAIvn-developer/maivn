# pyright: strict
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import cast

import pytest
from maivn_shared import (
    FINAL_EVENT_NAME,
    SYSTEM_TOOL_CHUNK_EVENT_NAME,
    HumanMessage,
    SessionClientProtocol,
)
from pydantic import JsonValue

from maivn._internal.adapters.networking import StreamingSSEClient
from maivn._internal.api.agent import Agent
from maivn._internal.core import SSEEvent
from maivn._internal.core.application_services.events.event_stream_processor import (
    EventStreamProcessor,
)
from maivn._internal.core.application_services.session.session_service import SessionService
from maivn._internal.core.orchestrator.core import AgentOrchestrator


@dataclass(frozen=True)
class _ServerConfig:
    base_url: str = "http://example.local"
    timeout_seconds: float = 1.0
    max_retries: int = 1


@dataclass(frozen=True)
class _ExecutionConfig:
    default_timeout_seconds: float = 1.0
    pending_event_timeout_seconds: float = 0.1
    enable_background_execution: bool = True


@dataclass(frozen=True)
class _Config:
    server: _ServerConfig
    execution: _ExecutionConfig


@dataclass
class _StubClient:
    """Duck-typed SessionClientProtocol stub for orchestrator end-to-end tests."""

    base_url: str = "http://example.local"
    timeout: float | None = None
    api_key: str | None = None
    payloads: list[dict[str, JsonValue]] = field(default_factory=list)

    def headers(self) -> dict[str, str]:
        return {}

    def start_session(self, *, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        self.payloads.append(payload)
        return {
            "session_id": "sess-1",
            "assistant_id": "assist-1",
            "events_url": "http://example.local/events",
            "resume_url": "http://example.local/resume",
        }

    def get_thread_id(self, create_if_missing: bool = False) -> str | None:
        _ = create_if_missing
        return None

    def set_thread_id(self, thread_id: str) -> None:
        _ = thread_id

    def get_tool_execution_timeout(self) -> float | None:
        return None


class _StubAgent:
    """Duck-typed Agent stub for orchestrator end-to-end tests."""

    api_key: str | None = None
    timeout: float | None = None
    max_results: int | None = None
    name: str = "stub-agent"
    description: str | None = None
    id: str = "agent-1"

    def compile_tools(self) -> None:
        return None

    def list_tools(self) -> list[object]:
        return []

    def get_swarm(self) -> object | None:
        return None


class _StubSseClient:
    """Duck-typed StreamingSSEClient stub for orchestrator end-to-end tests."""

    _events: list[SSEEvent]

    def __init__(self, events: list[SSEEvent]) -> None:
        self._events = events

    def iter_events(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[SSEEvent]:
        _ = (url, headers)
        return iter(self._events)


def _set_stub_sse_client(orchestrator: AgentOrchestrator, stub: _StubSseClient) -> None:
    """Install the stub SSE client into the orchestrator and its event coordinator.

    AgentOrchestrator declares ``_sse_client: StreamingSSEClient`` so we cast
    through ``object`` (per the policy escape hatch) to satisfy the strict
    type checker — the runtime depends only on ``iter_events``, which the
    stub provides. setattr keeps the protected-attribute access out of the
    reportPrivateUsage path while preserving the test wiring intent.
    """
    cast_stub = cast(StreamingSSEClient, cast(object, stub))
    # ``setattr`` keeps the protected-attribute access out of the
    # ``reportPrivateUsage`` path while preserving the wiring intent.
    setattr(orchestrator, "_sse_client", cast_stub)  # noqa: B010
    event_coordinator = cast(object, getattr(orchestrator, "_event_coordinator"))  # noqa: B009
    setattr(event_coordinator, "_sse_client", cast_stub)  # noqa: B010


def test_agent_orchestrator_invoke_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _Config(server=_ServerConfig(), execution=_ExecutionConfig())
    monkeypatch.setattr(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        lambda: config,
    )

    client = _StubClient()
    agent = _StubAgent()
    session_service = SessionService()
    event_processor = EventStreamProcessor(pending_event_timeout_s=0.1)

    orchestrator = AgentOrchestrator(
        cast(Agent, cast(object, agent)),
        client=cast(SessionClientProtocol, cast(object, client)),
        session_service=session_service,
        event_stream_processor=event_processor,
        logger=None,
    )

    _set_stub_sse_client(
        orchestrator,
        _StubSseClient(
            [
                SSEEvent(
                    name=FINAL_EVENT_NAME,
                    payload={"status": "completed", "responses": ["ok"]},
                )
            ]
        ),
    )

    response = orchestrator.invoke([HumanMessage(content="hi")], thread_id="thread-1")

    assert response.status == "completed"
    assert response.responses == ["ok"]
    assert client.payloads
    payload = client.payloads[0]
    assert payload["thread_id"] == "thread-1"
    assert "state" in payload


def test_agent_orchestrator_stream_yields_events(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _Config(server=_ServerConfig(), execution=_ExecutionConfig())
    monkeypatch.setattr(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        lambda: config,
    )

    client = _StubClient()
    agent = _StubAgent()
    session_service = SessionService()
    event_processor = EventStreamProcessor(pending_event_timeout_s=0.1)

    orchestrator = AgentOrchestrator(
        cast(Agent, cast(object, agent)),
        client=cast(SessionClientProtocol, cast(object, client)),
        session_service=session_service,
        event_stream_processor=event_processor,
        logger=None,
    )

    _set_stub_sse_client(
        orchestrator,
        _StubSseClient(
            [
                SSEEvent(
                    name=SYSTEM_TOOL_CHUNK_EVENT_NAME,
                    payload={
                        "tool_name": "think",
                        "assignment_id": "a-1",
                        "chunk_count": 1,
                        "elapsed_seconds": 0.1,
                        "text": "hello",
                    },
                ),
                SSEEvent(
                    name=FINAL_EVENT_NAME,
                    payload={"status": "completed", "responses": ["ok"]},
                ),
            ]
        ),
    )

    streamed = list(orchestrator.stream([HumanMessage(content="hi")], thread_id="thread-1"))

    assert [event.name for event in streamed] == [SYSTEM_TOOL_CHUNK_EVENT_NAME, FINAL_EVENT_NAME]
    first_payload = streamed[0].payload
    assert isinstance(first_payload, dict)
    assert first_payload.get("text") == "hello"
    second_payload = streamed[1].payload
    assert isinstance(second_payload, dict)
    assert second_payload.get("responses") == ["ok"]
