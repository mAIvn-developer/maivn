# pyright: strict
from __future__ import annotations

from maivn_shared import FINAL_EVENT_NAME, TOOL_EVENT_NAME, UPDATE_EVENT_NAME
from pydantic import JsonValue

from maivn._internal.core import SSEEvent, ToolEventPayload, ToolEventValue
from maivn._internal.core.application_services.events.event_stream_processor import (
    EventStreamHandlers,
    EventStreamProcessor,
)

JsonObject = dict[str, JsonValue]


def _coerce_passthrough(payload: JsonValue) -> JsonObject:
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def test_event_stream_processor_routes_tool_calls_and_final() -> None:
    submitted: list[tuple[str, JsonObject]] = []

    def _submit(event_id: str, payload: JsonObject, resume_url: str) -> None:
        _ = resume_url
        submitted.append((event_id, payload))

    def _no_op_requests(pending: dict[str, ToolEventPayload], resume_url: str) -> None:
        _ = (pending, resume_url)

    def _no_op_batch(event_id: str, value: ToolEventValue, resume_url: str) -> None:
        _ = (event_id, value, resume_url)

    def _no_op_ack(event_id: str, resume_url: str) -> None:
        _ = (event_id, resume_url)

    handlers = EventStreamHandlers(
        coerce_payload=_coerce_passthrough,
        process_tool_requests=_no_op_requests,
        process_tool_batch=_no_op_batch,
        submit_tool_call=_submit,
        acknowledge_barrier=_no_op_ack,
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
    pending_calls: list[dict[str, ToolEventPayload]] = []

    def process_pending(pending: dict[str, ToolEventPayload], resume_url: str) -> None:
        _ = resume_url
        pending_calls.append(pending)

    def _no_op_batch(event_id: str, value: ToolEventValue, resume_url: str) -> None:
        _ = (event_id, value, resume_url)

    def _no_op_submit(event_id: str, payload: JsonObject, resume_url: str) -> None:
        _ = (event_id, payload, resume_url)

    def _no_op_ack(event_id: str, resume_url: str) -> None:
        _ = (event_id, resume_url)

    handlers = EventStreamHandlers(
        coerce_payload=_coerce_passthrough,
        process_tool_requests=process_pending,
        process_tool_batch=_no_op_batch,
        submit_tool_call=_no_op_submit,
        acknowledge_barrier=_no_op_ack,
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

    def _no_op_requests(pending: dict[str, ToolEventPayload], resume_url: str) -> None:
        _ = (pending, resume_url)

    def _no_op_batch(event_id: str, value: ToolEventValue, resume_url: str) -> None:
        _ = (event_id, value, resume_url)

    def _no_op_submit(event_id: str, payload: JsonObject, resume_url: str) -> None:
        _ = (event_id, payload, resume_url)

    def _no_op_ack(event_id: str, resume_url: str) -> None:
        _ = (event_id, resume_url)

    handlers = EventStreamHandlers(
        coerce_payload=_coerce_passthrough,
        process_tool_requests=_no_op_requests,
        process_tool_batch=_no_op_batch,
        submit_tool_call=_no_op_submit,
        acknowledge_barrier=_no_op_ack,
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
    seen_updates: list[JsonObject] = []

    def _no_op_requests(pending: dict[str, ToolEventPayload], resume_url: str) -> None:
        _ = (pending, resume_url)

    def _no_op_batch(event_id: str, value: ToolEventValue, resume_url: str) -> None:
        _ = (event_id, value, resume_url)

    def _no_op_submit(event_id: str, payload: JsonObject, resume_url: str) -> None:
        _ = (event_id, payload, resume_url)

    def _no_op_ack(event_id: str, resume_url: str) -> None:
        _ = (event_id, resume_url)

    handlers = EventStreamHandlers(
        coerce_payload=_coerce_passthrough,
        process_tool_requests=_no_op_requests,
        process_tool_batch=_no_op_batch,
        submit_tool_call=_no_op_submit,
        acknowledge_barrier=_no_op_ack,
        handle_action_update=seen_updates.append,
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
