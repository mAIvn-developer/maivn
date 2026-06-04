"""Event handler implementations for SSE event stream processing.

This module contains the core event handlers: tool events, heartbeat,
interrupts, and model tool completion.  System-tool, update, status,
final, and enrichment handlers live in ``system_tool_handlers``.
"""

# pyright: strict
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from maivn_shared import (
    ENRICHMENT_EVENT_NAME,
    FINAL_EVENT_NAME,
    HEARTBEAT_EVENT_NAME,
    INTERRUPT_REQUEST_EVENT_NAME,
    INTERRUPT_REQUIRED_EVENT_NAME,
    MODEL_TOOL_COMPLETE_EVENT_NAME,
    PROGRESS_UPDATE_EVENT_NAME,
    STATUS_MESSAGE_EVENT_NAME,
    SYSTEM_TOOL_CHUNK_EVENT_NAME,
    SYSTEM_TOOL_COMPLETE_EVENT_NAME,
    SYSTEM_TOOL_ERROR_EVENT_NAME,
    SYSTEM_TOOL_START_EVENT_NAME,
    TOOL_EVENT_NAME,
    UPDATE_EVENT_NAME,
    ToolCall,
)
from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

from maivn._internal.core import SSEEvent, ToolEventPayload, ToolEventValue

# MARK: Types

JsonObject = dict[str, JsonValue]


class EventStreamHandlerCallbacks(Protocol):
    """Callback surface consumed by individual event handlers."""

    coerce_payload: Callable[[JsonValue], JsonObject]
    process_tool_requests: Callable[[dict[str, ToolEventPayload], str], None]
    process_tool_batch: Callable[[str, ToolEventValue, str], None]
    submit_tool_call: Callable[[str, JsonObject, str], None]
    acknowledge_barrier: Callable[[str, str], None]
    handle_user_input_request: Callable[[str, JsonObject, str], None] | None
    handle_interrupt_required: Callable[[JsonObject, str], None] | None
    handle_model_tool_complete: Callable[[JsonObject], None] | None
    handle_system_tool_start: Callable[[JsonObject], None] | None
    handle_system_tool_chunk: Callable[[JsonObject], None] | None
    handle_system_tool_complete: Callable[[JsonObject], None] | None
    handle_system_tool_error: Callable[[JsonObject], None] | None
    handle_action_update: Callable[[JsonObject], None] | None
    handle_status_message: Callable[[JsonObject], None] | None
    handle_enrichment: Callable[[JsonObject], None] | None


# MARK: Event Processing State


@dataclass
class EventProcessingState:
    """Mutable state container for event processing loop."""

    pending_tool_events: dict[str, ToolEventPayload]
    last_tool_event_time: float
    final_payload: JsonObject | None
    first_event_logged: bool

    @classmethod
    def create(cls) -> EventProcessingState:
        """Create a new event processing state with default values."""
        return cls(
            pending_tool_events={},
            last_tool_event_time=0.0,
            final_payload=None,
            first_event_logged=False,
        )

    def clear_pending(self) -> None:
        """Clear pending tool events and reset timestamp."""
        self.pending_tool_events = {}
        self.last_tool_event_time = 0.0


# MARK: Event Handler Map

EVENT_HANDLER_MAP: dict[str, str] = {
    HEARTBEAT_EVENT_NAME: "_handle_heartbeat",
    TOOL_EVENT_NAME: "_handle_tool_event",
    INTERRUPT_REQUEST_EVENT_NAME: "_handle_interrupt_request",
    INTERRUPT_REQUIRED_EVENT_NAME: "_handle_interrupt_required",
    MODEL_TOOL_COMPLETE_EVENT_NAME: "_handle_model_tool_complete",
    SYSTEM_TOOL_START_EVENT_NAME: "_handle_system_tool_start",
    SYSTEM_TOOL_CHUNK_EVENT_NAME: "_handle_system_tool_chunk",
    SYSTEM_TOOL_COMPLETE_EVENT_NAME: "_handle_system_tool_complete",
    SYSTEM_TOOL_ERROR_EVENT_NAME: "_handle_system_tool_error",
    PROGRESS_UPDATE_EVENT_NAME: "_handle_progress_update",
    STATUS_MESSAGE_EVENT_NAME: "_handle_status_message",
    UPDATE_EVENT_NAME: "_handle_update_event",
    FINAL_EVENT_NAME: "_handle_final_event",
    ENRICHMENT_EVENT_NAME: "_handle_enrichment",
}


# MARK: Heartbeat Handler


def handle_heartbeat(
    resume_url: str,
    handlers: EventStreamHandlerCallbacks,
    state: EventProcessingState,
    pending_event_timeout_s: float,
    logger: LoggerProtocol,
) -> None:
    """Handle heartbeat event, flushing pending events if timeout exceeded."""
    if not state.pending_tool_events or not state.last_tool_event_time:
        return

    if time.time() - state.last_tool_event_time > pending_event_timeout_s:
        logger.debug(
            "Heartbeat timeout reached; processing %s pending tool event(s)",
            len(state.pending_tool_events),
        )
        handlers.process_tool_requests(state.pending_tool_events, resume_url)
        state.clear_pending()


# MARK: Tool Event Handler


def handle_tool_event(
    event: SSEEvent,
    resume_url: str,
    handlers: EventStreamHandlerCallbacks,
    state: EventProcessingState,
    logger: LoggerProtocol,
) -> None:
    """Handle tool event, routing to appropriate sub-handler."""
    payload = cast(ToolEventPayload, cast(object, handlers.coerce_payload(event.payload)))
    tool_event_id = str(payload.get("id", ""))

    if not tool_event_id:
        logger.warning("Received tool event without id: %s", event.payload)
        return

    handled = route_tool_event(
        tool_event_id, payload, resume_url, state.pending_tool_events, handlers, logger
    )
    state.last_tool_event_time = 0.0 if handled else time.time()


def route_tool_event(
    tool_event_id: str,
    payload: ToolEventPayload,
    resume_url: str,
    pending_tool_events: dict[str, ToolEventPayload],
    handlers: EventStreamHandlerCallbacks,
    logger: LoggerProtocol,
) -> bool:
    """Route a tool event to the appropriate handler.

    Returns:
        True if the event was handled immediately, False if queued.
    """
    value_obj = cast(object, payload.get("value", {}))
    if not isinstance(value_obj, dict):
        pending_tool_events[tool_event_id] = payload
        return False
    value = cast(ToolEventValue, cast(object, value_obj))

    if value.get("tool_calls"):
        handlers.process_tool_batch(tool_event_id, value, resume_url)
        return True

    if value.get("barrier"):
        logger.debug("Barrier tool event acknowledged: %s", tool_event_id)
        handlers.acknowledge_barrier(tool_event_id, resume_url)
        return True

    if value.get("tool_call"):
        tool_call_payload = extract_tool_call_payload(value)
        handlers.submit_tool_call(tool_event_id, tool_call_payload, resume_url)
        return True

    pending_tool_events[tool_event_id] = payload
    return False


def extract_tool_call_payload(value: Mapping[str, object]) -> JsonObject:
    """Extract and normalize tool call payload from event value."""
    tool_call_value = value.get("tool_call", {})
    if isinstance(tool_call_value, ToolCall):
        tool_call_payload: JsonObject = {
            "tool_id": tool_call_value.tool_id,
            "args": tool_call_value.args,
        }
    elif isinstance(tool_call_value, Mapping):
        tool_call_payload = _json_mapping_to_dict(cast(Mapping[object, object], tool_call_value))
    else:
        tool_call_payload = {}

    if "private_data_injected" not in tool_call_payload:
        if "private_data_injected" in value:
            tool_call_payload["private_data_injected"] = cast(
                JsonValue, value["private_data_injected"]
            )
        elif "user_data_injected" in value:
            # "user_data_injected" is an alias for "private_data_injected".
            tool_call_payload["private_data_injected"] = cast(
                JsonValue, value["user_data_injected"]
            )

    if "interrupt_data_injected" not in tool_call_payload and "interrupt_data_injected" in value:
        tool_call_payload["interrupt_data_injected"] = cast(
            JsonValue, value["interrupt_data_injected"]
        )

    return tool_call_payload


# MARK: Interrupt Handlers


def handle_interrupt_request(
    event: SSEEvent,
    resume_url: str,
    handlers: EventStreamHandlerCallbacks,
    logger: LoggerProtocol,
) -> None:
    """Handle legacy interrupt request event."""
    if not handlers.handle_user_input_request:
        return

    payload = cast(ToolEventPayload, cast(object, handlers.coerce_payload(event.payload)))
    tool_event_id = str(payload.get("id", ""))
    value = cast(object, payload.get("value", {}))

    if isinstance(value, dict):
        value_payload = _json_mapping_to_dict(cast(dict[object, object], value))
        logger.info(
            "[USER_INPUT] Requesting input for tool=%s arg=%s",
            value_payload.get("tool_name"),
            value_payload.get("arg_name"),
        )
        handlers.handle_user_input_request(tool_event_id, value_payload, resume_url)


def handle_interrupt_required(
    event: SSEEvent,
    resume_url: str,
    handlers: EventStreamHandlerCallbacks,
    logger: LoggerProtocol,
) -> None:
    """Handle checkpoint-based interrupt required event."""
    if not handlers.handle_interrupt_required:
        return

    payload = handlers.coerce_payload(event.payload)
    logger.info(
        "[INTERRUPT] Checkpoint-based interrupt for tool=%s data_key=%s",
        payload.get("tool_name"),
        payload.get("data_key"),
    )
    handlers.handle_interrupt_required(payload, resume_url)


# MARK: Model Tool Handler


def handle_model_tool_complete(
    event: SSEEvent,
    handlers: EventStreamHandlerCallbacks,
    logger: LoggerProtocol,
) -> None:
    """Handle model tool completion event."""
    if not handlers.handle_model_tool_complete:
        return

    payload = handlers.coerce_payload(event.payload)
    tool_name = payload.get("tool_name", "")
    logger.debug("[MODEL_TOOL] Complete: %s", tool_name)
    handlers.handle_model_tool_complete(payload)


# MARK: Helpers


def _json_mapping_to_dict(value: Mapping[object, object]) -> JsonObject:
    """Copy a mapping into the JSON object shape used by SSE payload callbacks."""
    return {str(key): cast(JsonValue, item) for key, item in value.items()}


__all__ = [
    "EVENT_HANDLER_MAP",
    "EventProcessingState",
    "EventStreamHandlerCallbacks",
    "extract_tool_call_payload",
    "handle_heartbeat",
    "handle_interrupt_request",
    "handle_interrupt_required",
    "handle_model_tool_complete",
    "handle_tool_event",
    "route_tool_event",
]
