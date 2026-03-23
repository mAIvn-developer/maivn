from __future__ import annotations

from dataclasses import dataclass

from maivn_shared import FINAL_EVENT_NAME, SYSTEM_TOOL_CHUNK_EVENT_NAME, HumanMessage

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


class _StubClient:
    base_url: str = "http://example.local"
    timeout: float | None = None

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def headers(self) -> dict[str, str]:
        return {}

    def start_session(self, *, payload: dict) -> dict:
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

    def get_tool_execution_timeout(self) -> float | None:
        return None


class _StubAgent:
    api_key: str | None = None
    timeout: float | None = None
    max_results: int | None = None
    name: str = "stub-agent"
    description: str | None = None
    id: str = "agent-1"

    def compile_tools(self) -> None:
        return None

    def list_tools(self) -> list:
        return []

    def get_swarm(self):  # noqa: ANN001
        return None


class _StubSseClient:
    def __init__(self, events: list[SSEEvent]) -> None:
        self._events = events

    def iter_events(self, url: str, *, headers=None):  # noqa: ANN001
        _ = (url, headers)
        return iter(self._events)


def test_agent_orchestrator_invoke_end_to_end(monkeypatch) -> None:
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
        agent,
        client=client,
        session_service=session_service,
        event_stream_processor=event_processor,
        logger=None,
    )

    orchestrator._sse_client = _StubSseClient(
        [
            SSEEvent(
                name=FINAL_EVENT_NAME,
                payload={"status": "completed", "responses": ["ok"]},
            )
        ]
    )
    orchestrator._event_coordinator._sse_client = orchestrator._sse_client

    response = orchestrator.invoke([HumanMessage(content="hi")], thread_id="thread-1")

    assert response.status == "completed"
    assert response.responses == ["ok"]
    assert client.payloads
    payload = client.payloads[0]
    assert payload["thread_id"] == "thread-1"
    assert "state" in payload


def test_agent_orchestrator_stream_yields_events(monkeypatch) -> None:
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
        agent,
        client=client,
        session_service=session_service,
        event_stream_processor=event_processor,
        logger=None,
    )

    orchestrator._sse_client = _StubSseClient(
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
    )
    orchestrator._event_coordinator._sse_client = orchestrator._sse_client

    streamed = list(orchestrator.stream([HumanMessage(content="hi")], thread_id="thread-1"))

    assert [event.name for event in streamed] == [SYSTEM_TOOL_CHUNK_EVENT_NAME, FINAL_EVENT_NAME]
    assert streamed[0].payload.get("text") == "hello"
    assert streamed[1].payload.get("responses") == ["ok"]
