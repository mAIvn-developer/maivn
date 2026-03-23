"""Event handler implementations for SSE event stream processing.

This module contains the core event handlers: tool events, heartbeat,
interrupts, and model tool completion.  System-tool, update, status,
final, and enrichment handlers live in ``system_tool_handlers``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

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
)
from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core import SSEEvent, ToolEventPayload, ToolEventValue

if TYPE_CHECKING:
    from .event_stream_processor import EventStreamHandlers


# MARK: Event Processing State


@dataclass
class EventProcessingState:
    """Mutable state container for event processing loop."""

    pending_tool_events: dict[str, ToolEventPayload]
    last_tool_event_time: float
    final_payload: dict[str, Any] | None
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
    handlers: EventStreamHandlers,
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
    handlers: EventStreamHandlers,
    state: EventProcessingState,
    logger: LoggerProtocol,
) -> None:
    """Handle tool event, routing to appropriate sub-handler."""
    payload = cast(ToolEventPayload, handlers.coerce_payload(event.payload))
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
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> bool:
    """Route a tool event to the appropriate handler.

    Returns:
        True if the event was handled immediately, False if queued.
    """
    value = payload.get("value", {})
    if not isinstance(value, dict):
        pending_tool_events[tool_event_id] = payload
        return False

    if value.get("tool_calls"):
        handlers.process_tool_batch(tool_event_id, cast(ToolEventValue, value), resume_url)
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


def extract_tool_call_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Extract and normalize tool call payload from event value."""
    tool_call_payload: dict[str, Any] = dict(cast(dict[str, Any], value.get("tool_call", {})))

    if "private_data_injected" not in tool_call_payload:
        if "private_data_injected" in value:
            tool_call_payload["private_data_injected"] = value["private_data_injected"]
        elif "user_data_injected" in value:
            # Backward-compatible alias: user_data -> private_data
            tool_call_payload["private_data_injected"] = value["user_data_injected"]

    if "interrupt_data_injected" not in tool_call_payload and "interrupt_data_injected" in value:
        tool_call_payload["interrupt_data_injected"] = value["interrupt_data_injected"]

    return tool_call_payload


# MARK: Interrupt Handlers


def handle_interrupt_request(
    event: SSEEvent,
    resume_url: str,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle legacy interrupt request event."""
    if not handlers.handle_user_input_request:
        return

    payload = cast(ToolEventPayload, handlers.coerce_payload(event.payload))
    tool_event_id = str(payload.get("id", ""))
    value = payload.get("value", {})

    if isinstance(value, dict):
        logger.info(
            "[USER_INPUT] Requesting input for tool=%s arg=%s",
            value.get("tool_name"),
            value.get("arg_name"),
        )
        handlers.handle_user_input_request(tool_event_id, cast(dict[str, Any], value), resume_url)


def handle_interrupt_required(
    event: SSEEvent,
    resume_url: str,
    handlers: EventStreamHandlers,
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
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle model tool completion event."""
    if not handlers.handle_model_tool_complete:
        return

    payload = handlers.coerce_payload(event.payload)
    tool_name = payload.get("tool_name", "")
    logger.debug("[MODEL_TOOL] Complete: %s", tool_name)
    handlers.handle_model_tool_complete(payload)


__all__ = [
    "EVENT_HANDLER_MAP",
    "EventProcessingState",
    "extract_tool_call_payload",
    "handle_heartbeat",
    "handle_interrupt_request",
    "handle_interrupt_required",
    "handle_model_tool_complete",
    "handle_tool_event",
    "route_tool_event",
]
