"""Progress and event display methods for ``SimpleReporter``."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..._formatters import format_elapsed_time
from ..reporter_base import build_error_details, get_tool_prefix, is_reevaluate_system_tool
from .display_helpers import get_event_label, print_kv_lines, print_tool_child_lines

# MARK: Event Display


class SimpleReporterEventMixin:
    enabled: bool

    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Print an event message."""
        if not self.enabled:
            return

        normalized = str(event_type or "").strip().lower()

        if normalized in {"stream", "raw"}:
            print(message)
            if details:
                print_kv_lines(details=details)
            return

        label = get_event_label(normalized)
        print(f"[{label}] {message}")
        if details:
            print_kv_lines(details=details)


# MARK: Progress and Tool Display


class SimpleReporterProgressMixin:
    enabled: bool
    tracker: Any
    _progress_state: Any
    _truncate_result: Any
    print_event: Any

    @contextmanager
    def live_progress(self, description: str = "Processing...") -> Iterator[None]:
        """Context manager for progress display."""
        if self.enabled:
            print(f"{description}...")
        yield None

    def update_progress(self, task_id: Any, description: str | None = None) -> None:
        """Update progress (no-op for simple reporter)."""
        _ = (task_id, description)

    @contextmanager
    def prepare_for_user_input(self) -> Iterator[None]:
        """No-op context manager for user input preparation."""
        yield

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
    ) -> None:
        """Report tool execution completion."""
        tool_info = self.tracker.get_tool_info(event_id)
        if not tool_info:
            return

        if elapsed_ms is None:
            elapsed_ms = self.tracker.calculate_elapsed_ms(event_id)

        tool_type = tool_info.get("tool_type")
        tool_name = tool_info.get("name", "unknown")

        if is_reevaluate_system_tool(tool_name, tool_type):
            self.print_event("SYSTEM", "[OK] Reevaluating")
            return

        prefix = get_tool_prefix(tool_type)
        elapsed_str = format_elapsed_time(elapsed_ms) if elapsed_ms else ""
        message = f"Completed: {tool_name}"
        if elapsed_str:
            message += f" ({elapsed_str})"

        self.print_event(prefix, message)
        if tool_name.lower() != "reevaluate":
            print_tool_child_lines(result=result, truncate_fn=self._truncate_result)

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Report tool execution error."""
        details = build_error_details(error, event_id, elapsed_ms)
        self.print_event("ERROR", f"Failed: {tool_name}", details)

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        """Report system tool execution progress."""
        if not self.enabled:
            return

        now = time.monotonic()
        is_new_event = self._progress_state.last_event_id != event_id

        if is_new_event:
            self._progress_state.reset_for_event(event_id)

        safe_tool = str(tool_name or "").strip()
        if isinstance(text, str) and text:
            delta = self._progress_state.get_text_delta(event_id, text)
            if delta:
                for line in delta.splitlines():
                    print(line)
                return

        if not self._progress_state.should_emit_progress(is_new_event, now, chunk_count):
            return

        self._progress_state.update_emit_state(now, chunk_count)
        print(f"  [{safe_tool}] Processing... {elapsed_seconds:.0f}s chunks={chunk_count}")
