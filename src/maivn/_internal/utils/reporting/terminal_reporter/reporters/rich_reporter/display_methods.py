"""Display-oriented methods for ``RichReporter``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maivn_shared.utils.token_models import TokenUsage


# MARK: Display Methods


class RichReporterDisplayMixin:
    enabled: bool
    _terminal_lock: Any
    _display_manager: Any
    _progress_manager: Any
    _clear_assistant_stream_state: Any

    def print_header(self, title: str, subtitle: str = "") -> None:
        """Print a beautiful header."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_header(title, subtitle)

    def print_section(self, title: str, style: str = "bold cyan") -> None:
        """Print a section header."""
        if not self.enabled:
            return

        with self._terminal_lock:
            with self._progress_manager.prepare_for_user_input():
                self._display_manager.print_section(title, style)

    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Print an event with color coding."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_event(event_type, message, details)

    def report_phase_change(self, phase: str) -> None:
        """Report phase change."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_phase_change(phase)

    def print_summary(self, token_usage: TokenUsage | None = None) -> None:
        """Print execution summary."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_summary(token_usage)

    def print_final_result(self, result: Any) -> None:
        """Print final result in a copyable format."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._clear_assistant_stream_state()
            self._display_manager.print_final_result(result)

    def print_error_summary(self, error: str) -> None:
        """Print error summary."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._clear_assistant_stream_state()
            self._display_manager.print_error_summary(error)
