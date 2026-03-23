from __future__ import annotations

from typing import Any

from maivn_shared import FINAL_EVENT_NAME, TOOL_EVENT_NAME, UPDATE_EVENT_NAME

from maivn._internal.core import SSEEvent
from maivn._internal.core.application_services.events.event_stream_processor import (
    EventStreamHandlers,
    EventStreamProcessor,
)


def test_event_stream_processor_routes_tool_calls_and_final() -> None:
    submitted: list[tuple[str, dict[str, Any]]] = []

    handlers = EventStreamHandlers(
        coerce_payload=lambda payload: payload,
        process_tool_requests=lambda pending, resume_url: None,
        process_tool_batch=lambda event_id, value, resume_url: None,
        submit_tool_call=lambda event_id, payload, resume_url: submitted.append(
            (event_id, payload)
        ),
        acknowledge_barrier=lambda event_id, resume_url: None,
    )

    events = iter(
        [
            SSEEvent(
                name=TOOL_EVENT_NAME,
                payload={
                    "id": "evt-1",
                    "value": {"tool_call": {"tool_id": "tool-1", "args": {"a": 1}}},
                },
            ),
            SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "ok"}),
        ]
    )

    processor = EventStreamProcessor(pending_event_timeout_s=1.0)
    result = processor.consume(events=events, resume_url="http://resume", handlers=handlers)

    assert result == {"status": "ok"}
    assert submitted == [("evt-1", {"tool_id": "tool-1", "args": {"a": 1}})]


def test_event_stream_processor_flushes_pending_on_update() -> None:
    pending_calls: list[dict[str, Any]] = []

    def process_pending(pending: dict[str, Any], resume_url: str) -> None:
        pending_calls.append(pending)

    handlers = EventStreamHandlers(
        coerce_payload=lambda payload: payload,
        process_tool_requests=process_pending,
        process_tool_batch=lambda event_id, value, resume_url: None,
        submit_tool_call=lambda event_id, payload, resume_url: None,
        acknowledge_barrier=lambda event_id, resume_url: None,
    )

    events = iter(
        [
            SSEEvent(name=TOOL_EVENT_NAME, payload={"id": "evt-1", "value": {}}),
            SSEEvent(name=UPDATE_EVENT_NAME, payload={"expected_results": 1}),
            SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "ok"}),
        ]
    )

    processor = EventStreamProcessor(pending_event_timeout_s=1.0)
    result = processor.consume(events=events, resume_url="http://resume", handlers=handlers)

    assert result == {"status": "ok"}
    assert len(pending_calls) == 1
    assert "evt-1" in pending_calls[0]


def test_event_stream_processor_invokes_on_event_callback() -> None:
    seen_names: list[str] = []

    handlers = EventStreamHandlers(
        coerce_payload=lambda payload: payload,
        process_tool_requests=lambda pending, resume_url: None,
        process_tool_batch=lambda event_id, value, resume_url: None,
        submit_tool_call=lambda event_id, payload, resume_url: None,
        acknowledge_barrier=lambda event_id, resume_url: None,
    )

    events = iter(
        [
            SSEEvent(name=UPDATE_EVENT_NAME, payload={"expected_results": 0}),
            SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "ok"}),
        ]
    )

    processor = EventStreamProcessor(pending_event_timeout_s=1.0)
    result = processor.consume(
        events=events,
        resume_url="http://resume",
        handlers=handlers,
        on_event=lambda event: seen_names.append(event.name),
    )

    assert result == {"status": "ok"}
    assert seen_names == [UPDATE_EVENT_NAME, FINAL_EVENT_NAME]


def test_event_stream_processor_passes_update_payload_to_handler() -> None:
    seen_updates: list[dict[str, Any]] = []

    handlers = EventStreamHandlers(
        coerce_payload=lambda payload: payload,
        process_tool_requests=lambda pending, resume_url: None,
        process_tool_batch=lambda event_id, value, resume_url: None,
        submit_tool_call=lambda event_id, payload, resume_url: None,
        acknowledge_barrier=lambda event_id, resume_url: None,
        handle_action_update=lambda payload: seen_updates.append(payload),
    )

    events = iter(
        [
            SSEEvent(
                name=UPDATE_EVENT_NAME,
                payload={"assistant_id": "orchestrator_agent", "streaming_content": "hello"},
            ),
            SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "ok"}),
        ]
    )

    processor = EventStreamProcessor(pending_event_timeout_s=1.0)
    result = processor.consume(events=events, resume_url="http://resume", handlers=handlers)

    assert result == {"status": "ok"}
    assert seen_updates == [{"assistant_id": "orchestrator_agent", "streaming_content": "hello"}]
