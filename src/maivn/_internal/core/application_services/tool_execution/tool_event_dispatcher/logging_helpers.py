"""Logging helpers for ToolEventDispatcher."""

# pyright: strict
from __future__ import annotations

import time
from typing import Protocol

from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

# MARK: Protocols


class ToolEventDispatcherLogger(Protocol):
    """Dispatcher surface consumed by logging helpers."""

    @property
    def logger(self) -> LoggerProtocol:
        """Logger used for dispatcher diagnostics."""
        ...

    @staticmethod
    def summarize_injected_keys(payload: JsonValue) -> list[str]:
        """Summarize injected payload keys."""
        ...


# MARK: Logging


def log_tool_start(
    dispatcher: ToolEventDispatcherLogger,
    tool_id: str,
    tool_event_id: str,
    args: dict[str, JsonValue],
    *,
    private_data_injected: JsonValue,
    interrupt_data_injected: JsonValue,
) -> None:
    """Log tool execution start."""
    dispatcher.logger.info(
        "[CLIENT] [%f] Starting tool execution: %s (event_id=%s)",
        time.time(),
        tool_id,
        tool_event_id,
    )
    dispatcher.logger.info("[CLIENT] Args from server (keys only): %s", sorted(args.keys()))

    if private_data_injected:
        dispatcher.logger.info(
            "[CLIENT] Server says it injected private_data keys: %s",
            private_data_injected,
        )
    if interrupt_data_injected:
        dispatcher.logger.info(
            "[CLIENT] Server says it injected interrupt_data (keys only): %s",
            dispatcher.summarize_injected_keys(interrupt_data_injected),
        )


def log_tool_complete(
    dispatcher: ToolEventDispatcherLogger,
    tool_id: str,
    tool_event_id: str,
    elapsed_ms_value: int,
) -> None:
    """Log tool execution completion."""
    dispatcher.logger.info(
        "[CLIENT] [%f] Completed tool execution: %s (event_id=%s) elapsed_ms=%d",
        time.time(),
        tool_id,
        tool_event_id,
        elapsed_ms_value,
    )
