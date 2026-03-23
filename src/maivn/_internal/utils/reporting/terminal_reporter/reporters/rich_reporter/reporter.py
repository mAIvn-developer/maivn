"""Rich-based terminal reporter implementation."""

from __future__ import annotations

import threading

from ...base import BaseReporter
from .assistant_streaming import (
    RichReporterAssistantStreamingMixin,
    RichReporterFinalOutputMixin,
)
from .display import DisplayManager
from .display_methods import RichReporterDisplayMixin
from .progress import ProgressManager
from .progress_methods import RichReporterProgressMixin
from .reporting import ToolReporter
from .session_methods import RichReporterSessionMixin
from .terminal_setup import (
    InputHandler,
    configure_stdout_stderr_for_windows,
    create_console,
)
from .tool_methods import RichReporterSystemToolMixin, RichReporterToolMixin

# MARK: Rich Reporter


class RichReporter(
    RichReporterDisplayMixin,
    RichReporterProgressMixin,
    RichReporterSessionMixin,
    RichReporterToolMixin,
    RichReporterSystemToolMixin,
    RichReporterAssistantStreamingMixin,
    RichReporterFinalOutputMixin,
    BaseReporter,
):
    """Beautiful terminal reporter using rich library."""

    def __init__(self, enabled: bool = True) -> None:
        """Initialize the reporter."""
        self.enabled = enabled
        self._terminal_lock = threading.RLock()

        configure_stdout_stderr_for_windows()
        self.console = create_console()
        self._progress_manager = ProgressManager(self.console)
        self._tool_reporter = ToolReporter(self.console)
        self._input_handler = InputHandler(self.console)
        self._display_manager = DisplayManager(
            self.console,
            self._tool_reporter.tracker,
        )
        self._active_system_tool_event_ids: set[str] = set()
        self._system_tool_stream_group_event_ids: set[str] = set()
        self._assistant_stream_text_by_id: dict[str, str] = {}
        self._assistant_stream_live_suspended = False
