"""Reporter components for state tracking and file operations.
Provides the EventTracker for execution metrics and a FileWriter for large outputs.
Used by terminal reporter implementations."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import (
    LOGS_DIRECTORY,
    RESULT_FILENAME_PREFIX,
    TIMESTAMP_FORMAT,
)

# MARK: - EventTracker


class EventTracker:
    """Tracks tool execution events and metrics.

    This component maintains state about tool executions,
    including timing information and execution counts.

    Responsibilities:
    - Track tool execution count
    - Store tool execution metadata
    - Calculate execution timing
    - Manage current phase state
    """

    def __init__(self) -> None:
        """Initialize event tracker."""
        self._start_time: float = time.time()
        self._tools_executed: int = 0
        self._current_phase: str = "Initializing"
        self._tool_results: dict[str, dict[str, Any]] = {}

    # MARK: - Properties

    @property
    def tools_executed(self) -> int:
        """Get number of tools executed."""
        return self._tools_executed

    @property
    def current_phase(self) -> str:
        """Get current execution phase."""
        return self._current_phase

    # MARK: - Tool Recording

    def record_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        """Record tool execution start.

        Args:
            tool_name: Name of the tool
            event_id: Unique event identifier
            tool_type: Type of tool (func, agent, model, etc.)
            agent_name: Name of agent executing the tool (for multi-agent contexts)
        """
        self._tools_executed += 1
        self._tool_results[event_id] = {
            "name": tool_name,
            "start_time": time.time(),
            "tool_type": tool_type or "func",
            "agent_name": agent_name,
            "tool_args": tool_args,
        }

    def record_model_tool(self) -> None:
        """Record MODEL tool execution."""
        self._tools_executed += 1

    # MARK: - Tool Information

    def get_tool_info(self, event_id: str) -> dict[str, Any] | None:
        """Get tool execution information.

        Args:
            event_id: Event identifier

        Returns:
            Tool information dict or None if not found
        """
        return self._tool_results.get(event_id)

    # MARK: - Timing

    def calculate_elapsed_ms(self, event_id: str) -> int:
        """Calculate elapsed time for a tool execution.

        Args:
            event_id: Event identifier

        Returns:
            Elapsed time in milliseconds
        """
        tool_info = self._tool_results.get(event_id)
        if not tool_info:
            return 0

        return int((time.time() - tool_info["start_time"]) * 1000)

    def get_total_elapsed_seconds(self) -> float:
        """Get total elapsed time since tracker initialization.

        Returns:
            Elapsed time in seconds
        """
        return time.time() - self._start_time

    # MARK: - Phase Management

    def set_phase(self, phase: str) -> None:
        """Set current execution phase.

        Args:
            phase: Phase name
        """
        self._current_phase = phase

    # MARK: - Metrics

    def get_summary_metrics(self) -> dict[str, Any]:
        """Get summary metrics.

        Returns:
            Dictionary with tools_executed and elapsed_seconds
        """
        return {
            "tools_executed": self._tools_executed,
            "elapsed_seconds": self.get_total_elapsed_seconds(),
        }


# MARK: - FileWriter


class FileWriter:
    """Handles file output for large results.

    This component manages writing results to files when they
    are too large for terminal display.

    Responsibilities:
    - Create output directories
    - Generate timestamped filenames
    - Write content to files
    - Return file metadata
    """

    def __init__(self, logs_dir: str | None = None) -> None:
        """Initialize file writer.

        Args:
            logs_dir: Custom logs directory (defaults to config value)
        """
        self.logs_dir = Path(logs_dir or LOGS_DIRECTORY)

    # MARK: - File Operations

    def write_result(
        self,
        content: str,
        extension: str = "json",
    ) -> tuple[Path, int]:
        """Write result content to a file.

        Args:
            content: Content to write
            extension: File extension (without dot)

        Returns:
            Tuple of (file_path, file_size_bytes)
        """
        self._ensure_directory_exists()
        file_path = self._generate_file_path(extension)
        file_path.write_text(content, encoding="utf-8")

        return file_path, len(content)

    def _ensure_directory_exists(self) -> None:
        """Ensure the logs directory exists."""
        self.logs_dir.mkdir(exist_ok=True)

    def _generate_file_path(self, extension: str) -> Path:
        """Generate a timestamped file path.

        Args:
            extension: File extension (without dot)

        Returns:
            Path to the output file
        """
        timestamp = time.strftime(TIMESTAMP_FORMAT)
        filename = f"{RESULT_FILENAME_PREFIX}_{timestamp}.{extension}"
        return self.logs_dir / filename

    # MARK: - Content Analysis

    def should_write_to_file(
        self,
        content: str,
        max_lines: int | None = None,
        max_chars: int | None = None,
    ) -> bool:
        """Determine if content should be written to file.

        Args:
            content: Content to check
            max_lines: Maximum line count for inline display
            max_chars: Maximum character count for inline display

        Returns:
            True if content should be written to file
        """
        if max_lines is not None and self._exceeds_line_limit(content, max_lines):
            return True

        if max_chars is not None and self._exceeds_char_limit(content, max_chars):
            return True

        return False

    def _exceeds_line_limit(self, content: str, max_lines: int) -> bool:
        """Check if content exceeds line limit.

        Args:
            content: Content to check
            max_lines: Maximum allowed lines

        Returns:
            True if content exceeds limit
        """
        line_count = content.count("\n") + 1
        return line_count > max_lines

    def _exceeds_char_limit(self, content: str, max_chars: int) -> bool:
        """Check if content exceeds character limit.

        Args:
            content: Content to check
            max_chars: Maximum allowed characters

        Returns:
            True if content exceeds limit
        """
        return len(content) > max_chars

    def get_content_stats(self, content: str) -> dict[str, Any]:
        """Get statistics about content.

        Args:
            content: Content to analyze

        Returns:
            Dictionary with line_count and char_count
        """
        return {
            "line_count": content.count("\n") + 1,
            "char_count": len(content),
        }
