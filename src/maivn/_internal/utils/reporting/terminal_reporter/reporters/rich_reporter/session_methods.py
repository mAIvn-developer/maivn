"""Session lifecycle methods for ``RichReporter``."""

from __future__ import annotations

from typing import Any

# MARK: Session Methods


class RichReporterSessionMixin:
    enabled: bool
    _terminal_lock: Any
    _progress_manager: Any
    _tool_reporter: Any
    _display_manager: Any

    def report_session_start(
        self,
        session_id: str,
        assistant_id: str,
    ) -> None:
        """Report session start."""
        if not self.enabled:
            return

        with self._terminal_lock:
            with self._progress_manager.prepare_for_user_input():
                self._tool_reporter.report_session_start(session_id, assistant_id)

    def report_private_data(self, private_data: dict[str, Any]) -> None:
        """Report private data parameters."""
        if not self.enabled or not private_data:
            return

        with self._terminal_lock:
            self._display_manager.print_private_data(private_data)
