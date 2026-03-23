from __future__ import annotations

from maivn._internal.core import SessionEndpoints
from maivn._internal.core.application_services.events.interrupt_manager import (
    InterruptManager,
)
from maivn._internal.core.orchestrator.events import (
    EventConsumptionCoordinator,
    OrchestratorReporterHooks,
)


class _StubEventProcessor:
    def __init__(self, interrupt_manager: InterruptManager) -> None:
        self.calls = 0
        self._interrupt_manager = interrupt_manager

    def consume(self, *, events, resume_url, handlers, on_event=None):  # noqa: ANN001
        _ = (events, resume_url, handlers, on_event)
        self.calls += 1
        if self.calls == 1:
            self._interrupt_manager.store_resumed_session("resumed-1")
            raise RuntimeError("stream ended without a valid final payload")
        return {"status": "completed"}


class _StubToolEventDispatcher:
    def process_tool_requests(self, pending, resume_url):  # noqa: ANN001
        return None

    def process_tool_batch(self, tool_event_id, value, resume_url):  # noqa: ANN001
        return None

    def submit_tool_call(self, tool_event_id, payload, resume_url):  # noqa: ANN001
        return None

    def acknowledge_barrier(self, tool_event_id, resume_url):  # noqa: ANN001
        return None


class _StubInterruptHandler:
    def handle_user_input_request(self, tool_event_id, value, resume_url):  # noqa: ANN001
        return None

    def handle_interrupt_required(self, payload, resume_url):  # noqa: ANN001
        return None


class _StubSseClient:
    def iter_events(self, url: str, *, headers=None):  # noqa: ANN001
        _ = (url, headers)
        return iter([])


class _StubClient:
    base_url = "http://example.local"


def test_event_consumption_coordinator_chains_resumed_session() -> None:
    interrupt_manager = InterruptManager()
    event_processor = _StubEventProcessor(interrupt_manager)
    dispatcher = _StubToolEventDispatcher()
    interrupt_handler = _StubInterruptHandler()
    sse_client = _StubSseClient()
    reporter_hooks = OrchestratorReporterHooks(lambda: None)

    coordinator = EventConsumptionCoordinator(
        client=_StubClient(),
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
