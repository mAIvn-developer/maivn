"""Simple terminal reporter implementation."""

from __future__ import annotations

from ..._components import EventTracker, FileWriter
from ..._formatters import truncate_result
from ...base import BaseReporter
from ...config import (
    SIMPLE_BORDER_CHAR,
    SIMPLE_BOX_CORNERS,
    SIMPLE_BOX_HORIZONTAL,
    SIMPLE_BOX_VERTICAL,
)
from .assistant_streaming import SimpleReporterAssistantStreamingMixin
from .display_methods import SimpleReporterDisplayMixin
from .progress_methods import SimpleReporterEventMixin, SimpleReporterProgressMixin
from .progress_state import SystemToolProgressState
from .session_methods import SimpleReporterSessionMixin, SimpleReporterToolMixin

# MARK: Simple Reporter


class SimpleReporter(
    SimpleReporterDisplayMixin,
    SimpleReporterEventMixin,
    SimpleReporterProgressMixin,
    SimpleReporterSessionMixin,
    SimpleReporterToolMixin,
    SimpleReporterAssistantStreamingMixin,
    BaseReporter,
):
    """Simple terminal reporter without external dependencies."""

    def __init__(self, enabled: bool = True) -> None:
        """Initialize simple reporter."""
        self.enabled = enabled
        self.tracker = EventTracker()
        self.file_writer = FileWriter()

        self._progress_state = SystemToolProgressState()
        self._assistant_stream_text_by_id: dict[str, str] = {}
        self._assistant_stream_active = False

        self._border_char = SIMPLE_BORDER_CHAR
        self._box_corners = SIMPLE_BOX_CORNERS
        self._box_horizontal = SIMPLE_BOX_HORIZONTAL
        self._box_vertical = SIMPLE_BOX_VERTICAL
        self._truncate_result = truncate_result
