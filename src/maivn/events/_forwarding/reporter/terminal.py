"""Forwarders for terminal (final/error) events."""

from __future__ import annotations

from typing import Any

from ...._internal.core.orchestrator.helpers import sanitize_user_facing_error_message
from ..._models import AppEvent
from ..payload import coerce_mapping, string_value
from ..state import NormalizedEventForwardingState


def forward_final(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    reporter.print_event("success", "Agent execution completed successfully!")
    token_usage = coerce_mapping(payload.get("token_usage")) or coerce_mapping(
        getattr(event.output, "token_usage", None)
    )
    reporter.print_summary(token_usage=token_usage)

    response = string_value(payload.get("response")) or string_value(
        getattr(event.output, "response", None)
    )
    if response:
        reporter.print_final_response(response)
    reporter.print_final_result(payload.get("result", getattr(event.output, "result", None)))


def forward_error(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = (event, state)
    error_text = string_value(payload.get("error")) or string_value(
        getattr(event.error_info, "message", None)
    )
    if not error_text:
        error_text = "Unknown error"

    safe_message = sanitize_user_facing_error_message(error_text)
    reporter.print_event(
        "error",
        f"Agent execution failed: {safe_message}. Contact support if this persists.",
    )


__all__ = [
    "forward_error",
    "forward_final",
]
