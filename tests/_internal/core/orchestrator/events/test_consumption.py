# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

from maivn_shared import SessionClientProtocol
from pydantic import JsonValue

from maivn._internal.core import (
    SessionEndpoints,
    SSEEvent,
    ToolEventPayload,
    ToolEventValue,
)
from maivn._internal.core.application_services.events.interrupt_manager import (
    InterruptManager,
)
from maivn._internal.core.orchestrator.events import (
    EventConsumptionCoordinator,
    OrchestratorReporterHooks,
)
from maivn._internal.core.services import EventStreamHandlers

JsonObject = dict[str, JsonValue]


class _StubEventProcessor:
    def __init__(self, interrupt_manager: InterruptManager) -> None:
        self.calls: int = 0
        self._interrupt_manager: InterruptManager = interrupt_manager

    def consume(
        self,
        *,
        events: Iterator[SSEEvent],
        resume_url: str,
        handlers: EventStreamHandlers,
        on_event: Callable[[SSEEvent], None] | None = None,
    ) -> JsonObject:
        _ = (events, resume_url, handlers, on_event)
        self.calls += 1
        if self.calls == 1:
            self._interrupt_manager.store_resumed_session("resumed-1")
            raise RuntimeError("stream ended without a valid final payload")
        return {"status": "completed"}


class _StubToolEventDispatcher:
    def process_tool_requests(
        self,
        tool_events: dict[str, ToolEventPayload],
        resume_url: str,
    ) -> None:
        _ = (tool_events, resume_url)
        return None

    def process_tool_batch(
        self,
        tool_event_id: str,
        value: ToolEventValue,
        resume_url: str,
    ) -> None:
        _ = (tool_event_id, value, resume_url)
        return None

    def submit_tool_call(
        self,
        tool_event_id: str,
        tool_call_payload: JsonObject,
        resume_url: str,
    ) -> None:
        _ = (tool_event_id, tool_call_payload, resume_url)
        return None

    def acknowledge_barrier(self, tool_event_id: str, resume_url: str) -> None:
        _ = (tool_event_id, resume_url)
        return None


class _StubInterruptHandler:
    def handle_user_input_request(
        self,
        tool_event_id: str,
        value: JsonObject,
        resume_url: str,
    ) -> None:
        _ = (tool_event_id, value, resume_url)
        return None

    def handle_interrupt_required(self, interrupt_data: JsonObject, resume_url: str) -> None:
        _ = (interrupt_data, resume_url)
        return None


class _StubSseClient:
    def iter_events(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[SSEEvent]:
        _ = (url, headers)
        return iter([])


class _StubClient:
    """Stub client that intentionally omits `headers` to exercise the S10d NEW-OUT-1
    guarded `getattr` path in `EventConsumptionCoordinator._get_client_headers`."""

    base_url: str = "http://example.local"


def test_event_consumption_coordinator_chains_resumed_session() -> None:
    interrupt_manager = InterruptManager()
    event_processor = _StubEventProcessor(interrupt_manager)
    dispatcher = _StubToolEventDispatcher()
    interrupt_handler = _StubInterruptHandler()
    sse_client = _StubSseClient()
    reporter_hooks = OrchestratorReporterHooks(lambda: None)

    # _StubClient intentionally only implements `base_url` to exercise the
    # `_get_client_headers` getattr-guarded path (S10d NEW-OUT-1). The double-cast
    # through `object` is the policy-approved Pattern 2 boundary cast.
    stub_client = cast(SessionClientProtocol, cast(object, _StubClient()))

    coordinator = EventConsumptionCoordinator(
        client=stub_client,
        event_processor=event_processor,
        interrupt_manager=interrupt_manager,
        interrupt_service=object(),
        tool_event_dispatcher=dispatcher,
        interrupt_handler=interrupt_handler,
        sse_client=sse_client,
        reporter_hooks=reporter_hooks,
        set_reporter_context=lambda reporter, task: None,
    )

    endpoints = SessionEndpoints(
        session_id="sess-1",
        events_url="http://example.local/events",
        resume_url="http://example.local/resume",
    )

    result = coordinator.consume_events(endpoints, timeout=1.0, reporter=None)

    assert result["status"] == "completed"
    assert event_processor.calls == 2
    assert interrupt_manager.resumed_session_id is None
