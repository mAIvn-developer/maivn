# pyright: strict
from __future__ import annotations

from typing import cast

import pytest
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
from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

from maivn._internal.core import SSEEvent, ToolEventPayload, ToolEventValue
from maivn._internal.core.application_services.events.event_handlers import (
    EventProcessingState,
    JsonObject,
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
    """Recording fake that satisfies :class:`LoggerProtocol` for handler tests."""

    def __init__(self) -> None:
        self.debug_calls: list[str] = []
        self.info_calls: list[str] = []
        self.warning_calls: list[str] = []
        self.error_calls: list[str] = []
        self.exception_calls: list[str] = []
        self.critical_calls: list[str] = []

    def _format(self, message: str, args: tuple[object, ...]) -> str:
        return message % args if args else message

    def debug(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = component
        _ = metadata
        self.debug_calls.append(self._format(message, args))

    def info(self, message: str, *args: object, component: str = "APP", **metadata: object) -> None:
        _ = component
        _ = metadata
        self.info_calls.append(self._format(message, args))

    def warning(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = component
        _ = metadata
        self.warning_calls.append(self._format(message, args))

    def error(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = component
        _ = metadata
        self.error_calls.append(self._format(message, args))

    def exception(self, message: str, component: str = "APP", **metadata: object) -> None:
        _ = component
        _ = metadata
        self.exception_calls.append(message)

    def critical(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = component
        _ = metadata
        self.critical_calls.append(self._format(message, args))


def _typed_logger(logger: _Logger) -> LoggerProtocol:
    """Narrow the test logger to the protocol the SUT consumes."""
    return cast(LoggerProtocol, logger)


def _make_handlers(record: dict[str, list[object]]) -> EventStreamHandlers:
    """Build an ``EventStreamHandlers`` whose callbacks append into ``record``."""

    def _coerce(payload: JsonValue) -> JsonObject:
        return payload if isinstance(payload, dict) else {}

    def _process_tool_requests(pending: dict[str, ToolEventPayload], resume: str) -> None:
        record.setdefault("requests", []).append((pending.copy(), resume))

    def _process_tool_batch(tool_id: str, value: ToolEventValue, resume: str) -> None:
        record.setdefault("batch", []).append((tool_id, value, resume))

    def _submit_tool_call(tool_id: str, payload: JsonObject, resume: str) -> None:
        record.setdefault("calls", []).append((tool_id, payload, resume))

    def _acknowledge_barrier(tool_id: str, resume: str) -> None:
        record.setdefault("barrier", []).append((tool_id, resume))

    def _handle_user_input_request(tool_id: str, payload: JsonObject, resume: str) -> None:
        record.setdefault("input", []).append((tool_id, payload, resume))

    def _handle_interrupt_required(payload: JsonObject, resume: str) -> None:
        record.setdefault("interrupt", []).append((payload, resume))

    def _handle_model_tool_complete(payload: JsonObject) -> None:
        record.setdefault("model", []).append(payload)

    def _handle_system_tool_start(payload: JsonObject) -> None:
        record.setdefault("system_start", []).append(payload)

    def _handle_system_tool_chunk(payload: JsonObject) -> None:
        record.setdefault("system_chunk", []).append(payload)

    def _handle_system_tool_complete(payload: JsonObject) -> None:
        record.setdefault("system_complete", []).append(payload)

    def _handle_system_tool_error(payload: JsonObject) -> None:
        record.setdefault("system_error", []).append(payload)

    def _handle_action_update(payload: JsonObject) -> None:
        record.setdefault("action", []).append(payload)

    return EventStreamHandlers(
        coerce_payload=_coerce,
        process_tool_requests=_process_tool_requests,
        process_tool_batch=_process_tool_batch,
        submit_tool_call=_submit_tool_call,
        acknowledge_barrier=_acknowledge_barrier,
        handle_user_input_request=_handle_user_input_request,
        handle_interrupt_required=_handle_interrupt_required,
        handle_model_tool_complete=_handle_model_tool_complete,
        handle_system_tool_start=_handle_system_tool_start,
        handle_system_tool_chunk=_handle_system_tool_chunk,
        handle_system_tool_complete=_handle_system_tool_complete,
        handle_system_tool_error=_handle_system_tool_error,
        handle_action_update=_handle_action_update,
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
    record: dict[str, list[object]] = {}
    handlers = _make_handlers(record)
    logger = _typed_logger(_Logger())

    tool_calls_payload: ToolEventPayload = cast(
        ToolEventPayload,
        cast(object, {"value": {"tool_calls": [{"id": "1"}]}, "id": "id"}),
    )
    assert (
        route_tool_event(
            "id",
            tool_calls_payload,
            "resume",
            {},
            handlers,
            logger,
        )
        is True
    )

    barrier_payload: ToolEventPayload = cast(
        ToolEventPayload, cast(object, {"value": {"barrier": True}})
    )
    assert (
        route_tool_event(
            "id2",
            barrier_payload,
            "resume",
            {},
            handlers,
            logger,
        )
        is True
    )

    tool_call_payload: ToolEventPayload = cast(
        ToolEventPayload, cast(object, {"value": {"tool_call": {"x": 1}}})
    )
    assert (
        route_tool_event(
            "id3",
            tool_call_payload,
            "resume",
            {},
            handlers,
            logger,
        )
        is True
    )

    pending: dict[str, ToolEventPayload] = {}
    bad_payload: ToolEventPayload = cast(ToolEventPayload, cast(object, {"value": "bad"}))
    assert (
        route_tool_event(
            "id4",
            bad_payload,
            "resume",
            pending,
            handlers,
            logger,
        )
        is False
    )
    assert "id4" in pending


def test_handle_tool_event_queues_missing_id() -> None:
    record: dict[str, list[object]] = {}
    handlers = _make_handlers(record)
    logger = _Logger()
    state = EventProcessingState.create()

    handle_tool_event(
        SSEEvent(name=TOOL_EVENT_NAME, payload={"value": {}}),
        "resume",
        handlers,
        state,
        _typed_logger(logger),
    )

    assert logger.warning_calls


def test_handle_interrupt_request_and_required() -> None:
    record: dict[str, list[object]] = {}
    handlers = _make_handlers(record)
    logger = _typed_logger(_Logger())

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
    record: dict[str, list[object]] = {}
    handlers = _make_handlers(record)
    logger = _typed_logger(_Logger())

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
    state.pending_tool_events["tool"] = cast(ToolEventPayload, cast(object, {"id": "tool"}))
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
    record: dict[str, list[object]] = {}
    handlers = _make_handlers(record)
    logger = _typed_logger(_Logger())
    state = EventProcessingState.create()

    result = handle_final_event(
        SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "ok"}),
        handlers,
        state,
        logger,
    )

    assert result is True
    assert state.final_payload == {"status": "ok"}


def test_handle_heartbeat_flushes_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    record: dict[str, list[object]] = {}
    handlers = _make_handlers(record)
    logger = _typed_logger(_Logger())
    state = EventProcessingState.create()
    state.pending_tool_events = {"tool": cast(ToolEventPayload, cast(object, {"id": "tool"}))}
    state.last_tool_event_time = 1.0

    monkeypatch.setattr(
        "maivn._internal.core.application_services.events.event_handlers.time.time",
        lambda: 10.0,
    )

    handle_heartbeat("resume", handlers, state, pending_event_timeout_s=0.5, logger=logger)

    assert record["requests"]
    assert state.pending_tool_events == {}
