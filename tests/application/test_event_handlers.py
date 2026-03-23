from __future__ import annotations

from typing import Any

from maivn_shared import (
    FINAL_EVENT_NAME,
    INTERRUPT_REQUEST_EVENT_NAME,
    INTERRUPT_REQUIRED_EVENT_NAME,
    SYSTEM_TOOL_CHUNK_EVENT_NAME,
    SYSTEM_TOOL_COMPLETE_EVENT_NAME,
    SYSTEM_TOOL_ERROR_EVENT_NAME,
    SYSTEM_TOOL_START_EVENT_NAME,
    TOOL_EVENT_NAME,
    UPDATE_EVENT_NAME,
)

from maivn._internal.core import SSEEvent
from maivn._internal.core.application_services.events.event_handlers import (
    EventProcessingState,
    extract_tool_call_payload,
    handle_heartbeat,
    handle_interrupt_request,
    handle_interrupt_required,
    handle_tool_event,
    route_tool_event,
)
from maivn._internal.core.application_services.events.event_stream_processor import (
    EventStreamHandlers,
)
from maivn._internal.core.application_services.events.system_tool_handlers import (
    handle_final_event,
    handle_system_tool_chunk,
    handle_system_tool_complete,
    handle_system_tool_error,
    handle_system_tool_start,
    handle_update_event,
)


class _Logger:
    def __init__(self) -> None:
        self.debug_calls: list[str] = []
        self.info_calls: list[str] = []
        self.warning_calls: list[str] = []

    def debug(self, message: str, *args: Any) -> None:
        self.debug_calls.append(message % args if args else message)

    def info(self, message: str, *args: Any) -> None:
        self.info_calls.append(message % args if args else message)

    def warning(self, message: str, *args: Any) -> None:
        self.warning_calls.append(message % args if args else message)


def _make_handlers(record: dict[str, Any]) -> EventStreamHandlers:
    def _coerce(payload: Any) -> dict[str, Any]:
        return payload if isinstance(payload, dict) else {}

    return EventStreamHandlers(
        coerce_payload=_coerce,
        process_tool_requests=lambda pending, resume: record.setdefault("requests", []).append(
            (pending.copy(), resume)
        ),
        process_tool_batch=lambda tool_id, value, resume: record.setdefault("batch", []).append(
            (tool_id, value, resume)
        ),
        submit_tool_call=lambda tool_id, payload, resume: record.setdefault("calls", []).append(
            (tool_id, payload, resume)
        ),
        acknowledge_barrier=lambda tool_id, resume: record.setdefault("barrier", []).append(
            (tool_id, resume)
        ),
        handle_user_input_request=lambda tool_id, payload, resume: record.setdefault(
            "input", []
        ).append((tool_id, payload, resume)),
        handle_interrupt_required=lambda payload, resume: record.setdefault("interrupt", []).append(
            (payload, resume)
        ),
        handle_model_tool_complete=lambda payload: record.setdefault("model", []).append(payload),
        handle_system_tool_start=lambda payload: record.setdefault("system_start", []).append(
            payload
        ),
        handle_system_tool_chunk=lambda payload: record.setdefault("system_chunk", []).append(
            payload
        ),
        handle_system_tool_complete=lambda payload: record.setdefault("system_complete", []).append(
            payload
        ),
        handle_system_tool_error=lambda payload: record.setdefault("system_error", []).append(
            payload
        ),
        handle_action_update=lambda payload: record.setdefault("action", []).append(payload),
    )


def test_extract_tool_call_payload_handles_private_data_alias() -> None:
    payload = extract_tool_call_payload(
        {
            "tool_call": {"id": "1"},
            "user_data_injected": {"k": "v"},
            "interrupt_data_injected": {"x": 1},
        }
    )

    assert payload["private_data_injected"] == {"k": "v"}
    assert payload["interrupt_data_injected"] == {"x": 1}


def test_route_tool_event_branches() -> None:
    record: dict[str, Any] = {}
    handlers = _make_handlers(record)
    logger = _Logger()

    assert (
        route_tool_event(
            "id",
            {"value": {"tool_calls": [{"id": "1"}]}, "id": "id"},
            "resume",
            {},
            handlers,
            logger,
        )
        is True
    )

    assert (
        route_tool_event(
            "id2",
            {"value": {"barrier": True}},
            "resume",
            {},
            handlers,
            logger,
        )
        is True
    )

    assert (
        route_tool_event(
            "id3",
            {"value": {"tool_call": {"x": 1}}},
            "resume",
            {},
            handlers,
            logger,
        )
        is True
    )

    pending: dict[str, Any] = {}
    assert (
        route_tool_event(
            "id4",
            {"value": "bad"},
            "resume",
            pending,
            handlers,
            logger,
        )
        is False
    )
    assert "id4" in pending


def test_handle_tool_event_queues_missing_id() -> None:
    record: dict[str, Any] = {}
    handlers = _make_handlers(record)
    logger = _Logger()
    state = EventProcessingState.create()

    handle_tool_event(
        SSEEvent(name=TOOL_EVENT_NAME, payload={"value": {}}),
        "resume",
        handlers,
        state,
        logger,
    )

    assert logger.warning_calls


def test_handle_interrupt_request_and_required() -> None:
    record: dict[str, Any] = {}
    handlers = _make_handlers(record)
    logger = _Logger()

    event = SSEEvent(
        name=INTERRUPT_REQUEST_EVENT_NAME,
        payload={"id": "tool", "value": {"tool_name": "tool", "arg_name": "arg"}},
    )
    handle_interrupt_request(event, "resume", handlers, logger)
    assert record["input"]

    event_required = SSEEvent(name=INTERRUPT_REQUIRED_EVENT_NAME, payload={"tool_name": "tool"})
    handle_interrupt_required(event_required, "resume", handlers, logger)
    assert record["interrupt"]


def test_handle_system_tool_events_and_update() -> None:
    record: dict[str, Any] = {}
    handlers = _make_handlers(record)
    logger = _Logger()

    handle_system_tool_start(
        SSEEvent(name=SYSTEM_TOOL_START_EVENT_NAME, payload={"tool_name": "sys"}),
        handlers,
        logger,
    )
    handle_system_tool_chunk(
        SSEEvent(
            name=SYSTEM_TOOL_CHUNK_EVENT_NAME,
            payload={"tool_name": "sys", "chunk_count": 1, "text": "hi"},
        ),
        handlers,
        logger,
    )
    handle_system_tool_complete(
        SSEEvent(name=SYSTEM_TOOL_COMPLETE_EVENT_NAME, payload={"tool_name": "sys"}),
        handlers,
        logger,
    )
    handle_system_tool_error(
        SSEEvent(name=SYSTEM_TOOL_ERROR_EVENT_NAME, payload={"tool_name": "sys"}),
        handlers,
        logger,
    )

    state = EventProcessingState.create()
    state.pending_tool_events["tool"] = {"id": "tool"}
    state.last_tool_event_time = 1.0

    handle_update_event(
        SSEEvent(name=UPDATE_EVENT_NAME, payload={"expected_results": 1}),
        "resume",
        handlers,
        state,
        logger,
    )

    assert record["system_start"]
    assert record["system_chunk"]
    assert record["system_complete"]
    assert record["system_error"]
    assert record["requests"]


def test_handle_final_event_marks_completion() -> None:
    record: dict[str, Any] = {}
    handlers = _make_handlers(record)
    logger = _Logger()
    state = EventProcessingState.create()

    result = handle_final_event(
        SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "ok"}),
        handlers,
        state,
        logger,
    )

    assert result is True
    assert state.final_payload == {"status": "ok"}


def test_handle_heartbeat_flushes_pending(monkeypatch) -> None:
    record: dict[str, Any] = {}
    handlers = _make_handlers(record)
    logger = _Logger()
    state = EventProcessingState.create()
    state.pending_tool_events = {"tool": {"id": "tool"}}
    state.last_tool_event_time = 1.0

    monkeypatch.setattr(
        "maivn._internal.core.application_services.events.event_handlers.time.time", lambda: 10.0
    )

    handle_heartbeat("resume", handlers, state, pending_event_timeout_s=0.5, logger=logger)

    assert record["requests"]
    assert state.pending_tool_events == {}
