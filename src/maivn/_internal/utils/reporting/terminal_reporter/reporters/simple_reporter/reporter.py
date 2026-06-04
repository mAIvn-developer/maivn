"""Simple terminal reporter implementation."""

# pyright: strict

from __future__ import annotations

from collections.abc import Callable

from typing_extensions import override

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
    SimpleReporterEventMixin,
    SimpleReporterAssistantStreamingMixin,
    SimpleReporterDisplayMixin,
    SimpleReporterProgressMixin,
    SimpleReporterSessionMixin,
    SimpleReporterToolMixin,
    BaseReporter,
):
    """Simple terminal reporter without external dependencies."""

    @override
    def __init__(self, enabled: bool = True) -> None:
        """Initialize simple reporter."""
        BaseReporter.__init__(self, enabled=enabled)
        self.enabled: bool = enabled
        self.tracker: EventTracker = EventTracker()
        self.file_writer: FileWriter = FileWriter()

        self._progress_state: SystemToolProgressState = SystemToolProgressState()
        self._assistant_stream_text_by_id: dict[str, str] = {}
        self._assistant_stream_active: bool = False

        self._border_char: str = SIMPLE_BORDER_CHAR
        self._box_corners: tuple[str, str, str, str] = SIMPLE_BOX_CORNERS
        self._box_horizontal: str = SIMPLE_BOX_HORIZONTAL
        self._box_vertical: str = SIMPLE_BOX_VERTICAL
        self._truncate_result: Callable[[str], str] = truncate_result
