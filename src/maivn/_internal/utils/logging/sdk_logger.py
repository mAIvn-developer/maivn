"""Maivn SDK specialized logger with client-side methods.

This logger is designed for client-side SDK usage and provides simplified logging
for orchestration, session management, and tool execution on the client side.

Features:
- Automatic [MAIVN:COMPONENT] prefix
- Correlation ID support for request tracing
- Structured logging with context
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from maivn_shared.infrastructure.logging import MaivnLogger
from maivn_shared.infrastructure.logging.config import (
    DEFAULT_CONSOLE_LEVEL,
    DEFAULT_HUMAN_READABLE_CONSOLE,
    DEFAULT_USE_COLORS,
    LogLevel,
)

# MARK: Maivn SDK Logger


class MaivnSDKLogger(MaivnLogger):
    """Specialized logger for maivn SDK with client-side orchestration methods.

    This logger extends the base MaivnLogger with simplified methods for:
    - Session management
    - Orchestration events
    - Tool execution tracking
    - Automatic [MAIVN] component prefix

    By default, only console logging is enabled. Users can optionally provide
    a log file path to enable file logging.
    """

    _COMPONENT_PREFIX = "MAIVN"

    def _write_structured_log(
        self,
        level: LogLevel,
        component: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        """Override to add MAIVN prefix and correlation ID."""
        prefixed_component = (
            f"{self._COMPONENT_PREFIX}:{component}" if component else self._COMPONENT_PREFIX
        )

        enriched_data = self._enrich_with_correlation_id(data)

        super()._write_structured_log(
            level=level,
            component=prefixed_component,
            event=event,
            data=enriched_data,
        )

    def _enrich_with_correlation_id(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add correlation_id to data if not present."""
        if "correlation_id" in data:
            return data

        correlation_id = self.get_correlation_id()
        if correlation_id:
            return {**data, "correlation_id": correlation_id}
        return data

    # MARK: - Correlation ID Management

    def get_correlation_id(self) -> str | None:
        """Get current correlation ID from context."""
        context = self.get_context()
        correlation_id = context.get("correlation_id")
        return str(correlation_id) if correlation_id is not None else None

    def set_correlation_id(self, correlation_id: str | None = None) -> str:
        """Set correlation ID for request tracing."""
        resolved_id = correlation_id or str(uuid.uuid4())
        self.set_context(correlation_id=resolved_id)
        return resolved_id

    def clear_correlation_id(self) -> None:
        """Clear correlation ID from context."""
        self.clear_context("correlation_id")

    # MARK: - Session Logging

    def log_session_start(
        self,
        session_id: str,
        assistant_id: str,
        thread_id: str,
        **metadata: Any,
    ) -> None:
        """Log session start event."""
        self.set_context(session_id=session_id, thread_id=thread_id)
        self._write_structured_log(
            level="INFO",
            component="SESSION",
            event="session_start",
            data={
                "session_id": session_id,
                "assistant_id": assistant_id,
                "thread_id": thread_id,
                **metadata,
            },
        )

    def log_session_end(
        self,
        session_id: str,
        duration_ms: int | None = None,
        **metadata: Any,
    ) -> None:
        """Log session end event."""
        self._write_structured_log(
            level="INFO",
            component="SESSION",
            event="session_end",
            data={
                "session_id": session_id,
                "duration_ms": duration_ms,
                **metadata,
            },
        )
        self.clear_context("session_id", "thread_id")

    # MARK: - Orchestration Logging

    def log_orchestration(
        self,
        phase: Literal["start", "completed", "failed"],
        operation: str,
        **metadata: Any,
    ) -> None:
        """Log orchestration events."""
        level: LogLevel = "ERROR" if phase == "failed" else "INFO"
        self._write_structured_log(
            level=level,
            component="ORCHESTRATION",
            event=f"orchestration_{phase}",
            data={"operation": operation, **metadata},
        )

    # MARK: - Event Stream Logging

    def log_event_stream(
        self,
        event_type: str,
        event_data: dict[str, Any],
        **metadata: Any,
    ) -> None:
        """Log SSE event stream events."""
        event_keys = list(event_data.keys()) if isinstance(event_data, dict) else None
        self._write_structured_log(
            level="DEBUG",
            component="EVENT_STREAM",
            event=f"event_{event_type}",
            data={"event_type": event_type, "event_keys": event_keys, **metadata},
        )


# MARK: Global Instance

_logger_instance: MaivnSDKLogger | None = None


def _create_logger(log_file_path: Path | str | None = None) -> MaivnSDKLogger:
    """Create a new MaivnSDKLogger instance."""
    return MaivnSDKLogger(
        log_file_path=log_file_path,
        console_level=DEFAULT_CONSOLE_LEVEL,
        use_colors=DEFAULT_USE_COLORS,
        human_readable_console=DEFAULT_HUMAN_READABLE_CONSOLE,
    )


def get_logger(log_file_path: Path | str | None = None) -> MaivnSDKLogger:
    """Get the global maivn SDK logger instance.

    Logging behavior:
    - Console: OFF by default. Set MAIVN_LOG_LEVEL=INFO for console output.
    - File: All messages (DEBUG, INFO, WARNING, ERROR) if log_file_path provided
    """
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = _create_logger(log_file_path)
    return _logger_instance


def get_optional_logger() -> MaivnSDKLogger:
    """Get maivn SDK logger if available, creates default if not."""
    try:
        return get_logger()
    except Exception:  # pragma: no cover
        return _create_logger()


def configure_logging(log_file_path: Path | str | None = None) -> MaivnSDKLogger:
    """Configure maivn SDK logging before importing other maivn modules.

    This function MUST be called before importing Agent, Swarm, or other maivn
    components if you want file logging enabled.
    """
    if log_file_path is not None:
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    return get_logger(log_file_path=log_file_path)
