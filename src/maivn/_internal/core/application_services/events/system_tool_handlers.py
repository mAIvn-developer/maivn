"""System tool, update, status, and enrichment event handlers.

Extracted from event_handlers.py to keep each module focused
on a cohesive set of SSE event types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core import SSEEvent, UpdateEventPayload

if TYPE_CHECKING:
    from .event_handlers import EventProcessingState
    from .event_stream_processor import EventStreamHandlers


# MARK: System Tool Handlers


def handle_system_tool_start(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle system tool start event."""
    if not handlers.handle_system_tool_start:
        return

    payload = handlers.coerce_payload(event.payload)
    tool_name = payload.get("tool_name", "")
    logger.debug("[SYSTEM_TOOL] Start: %s", tool_name)
    handlers.handle_system_tool_start(payload)


def handle_system_tool_chunk(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle system tool streaming chunk event."""
    if not handlers.handle_system_tool_chunk:
        return

    payload = handlers.coerce_payload(event.payload)

    try:
        tool_name = payload.get("tool_name", "")
        chunk_count = payload.get("chunk_count")

        should_log = True
        if isinstance(chunk_count, int):
            should_log = chunk_count <= 5 or chunk_count % 200 == 0

        if should_log:
            elapsed_seconds = payload.get("elapsed_seconds")
            text = payload.get("text")
            text_len = len(text) if isinstance(text, str) else 0
            logger.debug(
                "[SYSTEM_TOOL] Chunk: %s count=%s elapsed=%s text_len=%s",
                tool_name,
                chunk_count,
                elapsed_seconds,
                text_len,
            )
    except Exception:
        pass

    handlers.handle_system_tool_chunk(payload)


def handle_system_tool_complete(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle system tool completion event."""
    if not handlers.handle_system_tool_complete:
        return

    payload = handlers.coerce_payload(event.payload)
    tool_name = payload.get("tool_name", "")
    logger.debug("[SYSTEM_TOOL] Complete: %s", tool_name)
    handlers.handle_system_tool_complete(payload)


def handle_system_tool_error(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle system tool error event."""
    if not handlers.handle_system_tool_error:
        return

    payload = handlers.coerce_payload(event.payload)
    tool_name = payload.get("tool_name", "")
    logger.debug("[SYSTEM_TOOL] Error: %s", tool_name)
    handlers.handle_system_tool_error(payload)


# MARK: Update Event Handlers


def handle_update_event(
    event: SSEEvent,
    resume_url: str,
    handlers: EventStreamHandlers,
    state: EventProcessingState,
    logger: LoggerProtocol,
) -> None:
    """Handle update event, triggering pending event processing."""
    update_payload = cast(UpdateEventPayload, handlers.coerce_payload(event.payload))

    if handlers.handle_action_update:
        handlers.handle_action_update(cast(dict[str, Any], update_payload))

    if not state.pending_tool_events:
        return

    expected_results = int(update_payload.get("expected_results", 0) or 0)
    logger.debug(
        "Update event: expecting %s results, have %s pending tool event(s)",
        expected_results,
        len(state.pending_tool_events),
    )
    handlers.process_tool_requests(state.pending_tool_events, resume_url)
    state.clear_pending()


def handle_progress_update(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle streamed assistant progress updates without flushing pending tool events.

    Unlike ``handle_update_event``, this handler does not flush pending tool
    events since progress updates are informational only.
    """
    update_payload = cast(UpdateEventPayload, handlers.coerce_payload(event.payload))

    if handlers.handle_action_update:
        handlers.handle_action_update(cast(dict[str, Any], update_payload))

    streaming_content = update_payload.get("streaming_content")
    assistant_id = update_payload.get("assistant_id")
    logger.debug(
        "[PROGRESS_UPDATE] assistant_id=%s text_len=%s",
        assistant_id,
        len(streaming_content) if isinstance(streaming_content, str) else 0,
    )


# MARK: Status Message Handler


def handle_status_message(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle a standalone status message emitted at swarm lifecycle milestones."""
    payload = cast(dict[str, Any], handlers.coerce_payload(event.payload))

    if handlers.handle_status_message:
        handlers.handle_status_message(payload)

    message = payload.get("message")
    assistant_id = payload.get("assistant_id")
    logger.debug(
        "[STATUS_MESSAGE] assistant_id=%s message=%s",
        assistant_id,
        message[:80] if isinstance(message, str) else None,
    )


# MARK: Final Event Handler


def handle_final_event(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    state: EventProcessingState,
    logger: LoggerProtocol,
) -> bool:
    """Handle final event, marking stream completion.

    Returns:
        True to signal processing should stop.
    """
    state.final_payload = handlers.coerce_payload(event.payload)
    logger.info("[EVENT_PROCESSOR] Received final event")
    logger.info(
        "[EVENT_PROCESSOR] Final payload keys: %s",
        list(state.final_payload.keys()) if isinstance(state.final_payload, dict) else "not a dict",
    )
    if isinstance(state.final_payload, dict):
        logger.info(
            "[EVENT_PROCESSOR] Final payload status: %s",
            state.final_payload.get("status"),
        )
    return True


# MARK: Enrichment Handler


def handle_enrichment(
    event: SSEEvent,
    handlers: EventStreamHandlers,
    logger: LoggerProtocol,
) -> None:
    """Handle enrichment phase change event."""
    if not handlers.handle_enrichment:
        return

    payload = handlers.coerce_payload(event.payload)
    phase = payload.get("phase", "")
    message = payload.get("message", "")
    logger.debug("[ENRICHMENT] Phase: %s - %s", phase, message)
    handlers.handle_enrichment(payload)


__all__ = [
    "handle_enrichment",
    "handle_final_event",
    "handle_progress_update",
    "handle_status_message",
    "handle_system_tool_chunk",
    "handle_system_tool_complete",
    "handle_system_tool_error",
    "handle_system_tool_start",
    "handle_update_event",
]
