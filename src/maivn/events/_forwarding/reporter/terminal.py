"""Forwarders for terminal (final/error) events."""

# pyright: strict
from __future__ import annotations

from typing import Protocol, cast

from ...._internal.core.orchestrator.helpers import sanitize_user_facing_error_message
from ..._models import AppEvent
from ..payload import EventPayload, coerce_mapping, string_value
from ..state import NormalizedEventForwardingState

# MARK: Reporter Protocol


class TerminalEventReporter(Protocol):
    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None: ...

    def print_summary(self, token_usage: object | None = None) -> None: ...

    def print_final_response(self, response: str) -> None: ...

    def print_final_result(self, result: object) -> None: ...


# MARK: Terminal Forwarding


def forward_final(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    terminal_reporter = cast(TerminalEventReporter, reporter)
    terminal_reporter.print_event("success", "Agent execution completed successfully!")
    token_usage = coerce_mapping(payload.get("token_usage")) or coerce_mapping(
        cast(object, event.output.token_usage) if event.output is not None else None
    )
    terminal_reporter.print_summary(token_usage=token_usage)

    response = string_value(payload.get("response")) or string_value(
        event.output.response if event.output is not None else None
    )
    if response:
        terminal_reporter.print_final_response(response)
    result = payload.get(
        "result",
        cast(object, event.output.result) if event.output is not None else None,
    )
    terminal_reporter.print_final_result(result)


def forward_error(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    _ = (event, state)
    terminal_reporter = cast(TerminalEventReporter, reporter)
    error_text = string_value(payload.get("error")) or string_value(
        event.error_info.message if event.error_info is not None else None
    )
    if not error_text:
        error_text = "Unknown error"

    safe_message = sanitize_user_facing_error_message(error_text)
    terminal_reporter.print_event(
        "error",
        f"Agent execution failed: {safe_message}. Contact support if this persists.",
    )


__all__ = [
    "forward_error",
    "forward_final",
]
