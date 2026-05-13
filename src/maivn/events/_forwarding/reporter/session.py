"""Forwarders for session lifecycle and assistant streaming events."""

from __future__ import annotations

from typing import Any

from ..._models import AppEvent
from ..payload import mapping_value, normalized_text, string_value
from ..state import NormalizedEventForwardingState


def forward_session_start(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    session_id = normalized_text(payload.get("session_id")) or normalized_text(
        getattr(event.session, "id", None)
    )
    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.session, "assistant_id", None)
    )
    if session_id and assistant_id:
        reporter.report_session_start(session_id, assistant_id)


def forward_assistant_chunk(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    delta = string_value(payload.get("text")) or string_value(
        getattr(event.assistant, "delta", None)
    )
    if not delta:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.assistant, "id", None)
    )
    stream_id = assistant_id or "assistant"
    previous = state.assistant_text_by_id.get(stream_id, "")
    full_text = previous + delta
    state.assistant_text_by_id[stream_id] = full_text

    reporter.report_response_chunk(
        delta,
        assistant_id=stream_id,
        full_text=full_text,
    )


def forward_status_message(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    message = string_value(payload.get("message")) or string_value(
        mapping_value(payload.get("status"), "message")
    )
    if not message:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.assistant, "id", None)
    )
    reporter.report_status_message(message, assistant_id=assistant_id or "assistant")


__all__ = [
    "forward_assistant_chunk",
    "forward_session_start",
    "forward_status_message",
]
