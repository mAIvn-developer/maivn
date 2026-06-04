"""Forwarders for session lifecycle and assistant streaming events."""

# pyright: strict
from __future__ import annotations

import inspect
from typing import Protocol, cast

from ..._models import AppEvent
from ..payload import EventPayload, mapping_value, normalized_text, string_value
from ..state import NormalizedEventForwardingState

# MARK: Reporter Protocol


class SessionEventReporter(Protocol):
    def report_session_start(self, session_id: str, assistant_id: str) -> None: ...

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
    ) -> None: ...

    def report_status_message(
        self,
        message: str,
        *,
        assistant_id: str | None = None,
    ) -> None: ...


# MARK: Session Forwarding


def forward_session_start(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    session_reporter = cast(SessionEventReporter, reporter)
    session = event.session
    session_id = normalized_text(payload.get("session_id")) or normalized_text(
        session.id if session is not None else None
    )
    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        session.assistant_id if session is not None else None
    )
    if session_id and assistant_id:
        session_reporter.report_session_start(session_id, assistant_id)


# MARK: Assistant Forwarding


def forward_assistant_chunk(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    session_reporter = cast(SessionEventReporter, reporter)
    assistant = event.assistant
    delta = string_value(payload.get("text")) or string_value(
        assistant.delta if assistant is not None else None
    )
    if not delta:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        assistant.id if assistant is not None else None
    )
    stream_id = assistant_id or "assistant"

    # ``replace_content`` is the normalize-layer signal that this chunk
    # represents a fresh stream (reevaluate cycle, synthesis restart) and
    # should overwrite the bubble downstream rather than append to whatever
    # the prior cycle left behind. The forwarding-state cache key on this
    # stream MUST be reset before we recompute ``full_text``; otherwise the
    # accumulated prior-cycle text gets prepended to the new cycle's first
    # chunk and the bubble keeps growing across cycles.
    replace_content = bool(payload.get("replace_content")) or bool(
        assistant.replace_content if assistant is not None else False
    )
    if replace_content:
        state.assistant_text_by_id[stream_id] = ""

    previous = state.assistant_text_by_id.get(stream_id, "")
    full_text = previous + delta
    state.assistant_text_by_id[stream_id] = full_text

    # Some reporter implementations predate the ``replace_content`` kwarg.
    if _report_response_chunk_accepts_keyword(session_reporter, "replace_content"):
        session_reporter.report_response_chunk(
            delta,
            assistant_id=stream_id,
            full_text=full_text,
            replace_content=replace_content,
        )
        return

    session_reporter.report_response_chunk(
        delta,
        assistant_id=stream_id,
        full_text=full_text,
    )


# MARK: Status Forwarding


def forward_status_message(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    session_reporter = cast(SessionEventReporter, reporter)
    message = string_value(payload.get("message")) or string_value(
        mapping_value(payload.get("status"), "message")
    )
    if not message:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        event.assistant.id if event.assistant is not None else None
    )
    session_reporter.report_status_message(message, assistant_id=assistant_id or "assistant")


def _report_response_chunk_accepts_keyword(
    reporter: SessionEventReporter,
    keyword: str,
) -> bool:
    try:
        params = inspect.signature(reporter.report_response_chunk).parameters
    except (TypeError, ValueError):
        return False
    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
    )
    return accepts_var_kwargs or keyword in params


__all__ = [
    "forward_assistant_chunk",
    "forward_session_start",
    "forward_status_message",
]
