"""Logging helpers for ToolEventDispatcher."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dispatcher import ToolEventDispatcher


# MARK: Logging


def log_tool_start(
    dispatcher: ToolEventDispatcher,
    tool_id: str,
    tool_event_id: str,
    args: dict[str, Any],
    *,
    private_data_injected: Any,
    interrupt_data_injected: Any,
) -> None:
    """Log tool execution start."""
    dispatcher._logger.info(
        "[CLIENT] [%f] Starting tool execution: %s (event_id=%s)",
        time.time(),
        tool_id,
        tool_event_id,
    )
    if isinstance(args, dict):
        dispatcher._logger.info("[CLIENT] Args from server (keys only): %s", sorted(args.keys()))
    else:
        dispatcher._logger.info(
            "[CLIENT] Args from server (type only): %s",
            type(args).__name__,
        )

    if private_data_injected:
        dispatcher._logger.info(
            "[CLIENT] Server says it injected private_data keys: %s",
            private_data_injected,
        )
    if interrupt_data_injected:
        dispatcher._logger.info(
            "[CLIENT] Server says it injected interrupt_data (keys only): %s",
            dispatcher._summarize_injected_keys(interrupt_data_injected),
        )


def log_tool_complete(
    dispatcher: ToolEventDispatcher,
    tool_id: str,
    tool_event_id: str,
    elapsed_ms_value: int,
) -> None:
    """Log tool execution completion."""
    dispatcher._logger.info(
        "[CLIENT] [%f] Completed tool execution: %s (event_id=%s) elapsed_ms=%d",
        time.time(),
        tool_id,
        tool_event_id,
        elapsed_ms_value,
    )
