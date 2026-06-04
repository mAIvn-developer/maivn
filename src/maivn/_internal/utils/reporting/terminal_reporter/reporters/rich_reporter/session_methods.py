"""Session lifecycle methods for ``RichReporter``."""

# pyright: strict
from __future__ import annotations

from abc import ABC
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from typing_extensions import override

from ...base.interface import BaseReporterInterface
from .display import DisplayManager
from .progress import ProgressManager
from .reporting import ToolReporter

# MARK: Session Methods


class RichReporterSessionMixin(BaseReporterInterface, ABC):
    if TYPE_CHECKING:
        enabled: bool
        _terminal_lock: AbstractContextManager[bool]
        _progress_manager: ProgressManager
        _tool_reporter: ToolReporter
        _display_manager: DisplayManager

    @override
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

    @override
    def report_private_data(
        self,
        private_data: dict[str, object],
    ) -> None:
        """Report private data parameters."""
        if not self.enabled or not private_data:
            return

        with self._terminal_lock:
            self._display_manager.print_private_data(private_data)
