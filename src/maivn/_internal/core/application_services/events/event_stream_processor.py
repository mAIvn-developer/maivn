"""Shared SSE event processing utilities for orchestrators."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from maivn_shared import (
    ASSIGNMENT_COMPLETED_EVENT_NAME,
    ASSIGNMENT_RECEIVED_EVENT_NAME,
)
from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core import SSEEvent, ToolEventPayload, ToolEventValue
from maivn._internal.utils.logging import get_optional_logger

from .event_handlers import (
    EVENT_HANDLER_MAP,
    EventProcessingState,
    handle_heartbeat,
    handle_interrupt_request,
    handle_interrupt_required,
    handle_model_tool_complete,
    handle_tool_event,
)
from .system_tool_handlers import (
    handle_enrichment,
    handle_final_event,
    handle_progress_update,
    handle_status_message,
    handle_system_tool_chunk,
    handle_system_tool_complete,
    handle_system_tool_error,
    handle_system_tool_start,
    handle_update_event,
)

# MARK: - EventStreamHandlers


@dataclass
class EventStreamHandlers:
    """Encapsulates all event stream callback handlers.

    This dataclass groups related callback functions used by EventStreamProcessor,
    reducing parameter count and improving API clarity.
    """

    coerce_payload: Callable[[Any], dict[str, Any]]
    """Coerce event payload to dictionary."""

    process_tool_requests: Callable[[dict[str, ToolEventPayload], str], None]
    """Process accumulated tool requests."""

    process_tool_batch: Callable[[str, ToolEventValue, str], None]
    """Process a batch of tool calls."""

    submit_tool_call: Callable[[str, dict[str, Any], str], None]
    """Submit a single tool call for execution."""

    acknowledge_barrier: Callable[[str, str], None]
    """Acknowledge a barrier event."""

    handle_user_input_request: Callable[[str, dict[str, Any], str], None] | None = None
    """Optional handler for user input requests (legacy interrupt_request)."""

    handle_interrupt_required: Callable[[dict[str, Any], str], None] | None = None
    """Optional handler for new checkpoint-based interrupts."""

    handle_model_tool_complete: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for MODEL tool execution completion notifications."""

    handle_system_tool_start: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for system tool start payload."""

    handle_system_tool_chunk: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for system tool streaming chunk (progress update)."""

    handle_system_tool_complete: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for system tool completion payload."""

    handle_system_tool_error: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for system tool error payload."""

    handle_action_update: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for action lifecycle update events."""

    handle_status_message: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for standalone status messages (swarm lifecycle milestones)."""

    handle_enrichment: Callable[[dict[str, Any]], None] | None = None
    """Optional handler for enrichment phase change events."""


# MARK: - EventStreamProcessor


class EventStreamProcessor:
    """Consumes SSE event streams and routes tool events.

    The processor uses a class-level event handler map to avoid recreation
    on every event, improving performance for high-volume event streams.
    """

    _IGNORED_EVENTS = frozenset({ASSIGNMENT_RECEIVED_EVENT_NAME, ASSIGNMENT_COMPLETED_EVENT_NAME})

    def __init__(
        self, *, logger: LoggerProtocol | None = None, pending_event_timeout_s: float
    ) -> None:
        """Initialize event stream processor.

        Args:
            logger: Optional logger instance
            pending_event_timeout_s: Timeout for pending events before forced processing
        """
        self._logger: LoggerProtocol = logger or get_optional_logger()
        self._pending_event_timeout_s = pending_event_timeout_s

    # MARK: - Public API

    def consume(
        self,
        *,
        events: Iterator[SSEEvent],
        resume_url: str,
        handlers: EventStreamHandlers,
        on_event: Callable[[SSEEvent], None] | None = None,
    ) -> dict[str, Any]:
        """Consume events until a final payload is observed.

        Args:
            events: Iterator of SSE events
            resume_url: URL for resuming the event stream
            handlers: Event stream handlers
            on_event: Optional callback invoked for each raw SSE event before processing

        Returns:
            The final event payload
        """
        state = EventProcessingState.create()
        t_start = time.perf_counter()

        for event in events:
            if on_event is not None:
                try:
                    on_event(event)
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning("Ignoring stream observer callback failure: %s", exc)
            self._log_first_event(event, t_start, state)

            if self._handle_event(event, resume_url, handlers, state):
                break

        self._flush_pending_events(state.pending_tool_events, resume_url, handlers)
        return self._validate_final_payload(state.final_payload)

    # MARK: - Event Dispatch

    def _handle_event(
        self,
        event: SSEEvent,
        resume_url: str,
        handlers: EventStreamHandlers,
        state: EventProcessingState,
    ) -> bool:
        """Route event to appropriate handler.

        Args:
            event: The SSE event
            resume_url: URL for resuming the event stream
            handlers: Event stream handlers
            state: Current processing state

        Returns:
            True if processing should stop
        """
        handler_name = EVENT_HANDLER_MAP.get(event.name)

        if handler_name:
            return self._dispatch_handler(handler_name, event, resume_url, handlers, state)

        if event.name not in self._IGNORED_EVENTS:
            self._logger.debug("Ignoring unrecognized event '%s'", event.name)
        return False

    def _dispatch_handler(
        self,
        handler_name: str,
        event: SSEEvent,
        resume_url: str,
        handlers: EventStreamHandlers,
        state: EventProcessingState,
    ) -> bool:
        """Dispatch to the appropriate handler function.

        Args:
            handler_name: Name of the handler method
            event: The SSE event
            resume_url: URL for resuming the event stream
            handlers: Event stream handlers
            state: Current processing state

        Returns:
            True if processing should stop
        """
        if handler_name == "_handle_heartbeat":
            handle_heartbeat(
                resume_url, handlers, state, self._pending_event_timeout_s, self._logger
            )
            return False

        if handler_name == "_handle_tool_event":
            handle_tool_event(event, resume_url, handlers, state, self._logger)
            return False

        if handler_name == "_handle_interrupt_request":
            handle_interrupt_request(event, resume_url, handlers, self._logger)
            return False

        if handler_name == "_handle_interrupt_required":
            handle_interrupt_required(event, resume_url, handlers, self._logger)
            return False

        if handler_name == "_handle_model_tool_complete":
            handle_model_tool_complete(event, handlers, self._logger)
            return False

        if handler_name == "_handle_system_tool_start":
            handle_system_tool_start(event, handlers, self._logger)
            return False

        if handler_name == "_handle_system_tool_chunk":
            handle_system_tool_chunk(event, handlers, self._logger)
            return False

        if handler_name == "_handle_system_tool_complete":
            handle_system_tool_complete(event, handlers, self._logger)
            return False

        if handler_name == "_handle_system_tool_error":
            handle_system_tool_error(event, handlers, self._logger)
            return False

        if handler_name == "_handle_progress_update":
            handle_progress_update(event, handlers, self._logger)
            return False

        if handler_name == "_handle_status_message":
            handle_status_message(event, handlers, self._logger)
            return False

        if handler_name == "_handle_update_event":
            handle_update_event(event, resume_url, handlers, state, self._logger)
            return False

        if handler_name == "_handle_enrichment":
            handle_enrichment(event, handlers, self._logger)
            return False

        if handler_name == "_handle_final_event":
            return handle_final_event(event, handlers, state, self._logger)

        return False

    # MARK: - Helpers

    def _log_first_event(
        self,
        event: SSEEvent,
        t_start: float,
        state: EventProcessingState,
    ) -> None:
        """Log timing information for the first event.

        Args:
            event: The SSE event
            t_start: Start time of event consumption
            state: Current processing state
        """
        if state.first_event_logged:
            return

        t_first = time.perf_counter()
        self._logger.debug(
            "[TIMING] sse.first_event name=%s elapsed_ms=%d",
            event.name,
            int((t_first - t_start) * 1000.0),
        )
        state.first_event_logged = True

    def _flush_pending_events(
        self,
        pending_tool_events: dict[str, ToolEventPayload],
        resume_url: str,
        handlers: EventStreamHandlers,
    ) -> None:
        """Flush any remaining pending tool events.

        Args:
            pending_tool_events: Dictionary of pending tool events
            resume_url: URL for resuming the event stream
            handlers: Event stream handlers
        """
        if pending_tool_events:
            self._logger.debug(
                "Processing remaining %s tool event(s) before finishing",
                len(pending_tool_events),
            )
            handlers.process_tool_requests(pending_tool_events, resume_url)

    def _validate_final_payload(self, final_payload: dict[str, Any] | None) -> dict[str, Any]:
        """Validate and return the final payload.

        Args:
            final_payload: The final event payload

        Returns:
            Validated final payload

        Raises:
            RuntimeError: If no valid final payload was received
        """
        if not isinstance(final_payload, dict):
            raise RuntimeError("Session completed without a valid final payload")
        return final_payload


__all__ = ["EventStreamHandlers", "EventStreamProcessor"]
