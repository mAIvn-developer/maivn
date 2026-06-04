"""Assistant streaming methods for ``RichReporter``."""

# pyright: strict
from __future__ import annotations

from abc import ABC
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from typing_extensions import override

from ..._formatters import extract_text_from_response
from ...base.defaults import ReporterDefaultEventsMixin
from ...base.interface import BaseReporterInterface
from .display import DisplayManager
from .progress import ProgressManager

if TYPE_CHECKING:
    from rich.console import Console


# MARK: Assistant Streaming


class RichReporterAssistantStreamingMixin(ReporterDefaultEventsMixin, ABC):
    if TYPE_CHECKING:
        enabled: bool
        _terminal_lock: AbstractContextManager[bool]
        _progress_manager: ProgressManager
        _display_manager: DisplayManager
        _assistant_stream_text_by_id: dict[str, str]
        _assistant_stream_live_suspended: bool
        console: Console

    @override
    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
    ) -> None:
        """Render incremental assistant response text.

        ``replace_content`` is the SDK signal that this chunk represents a
        fresh stream that should overwrite the displayed bubble (e.g. after
        a reevaluate cycle or a mid-stream synthesis revision). Terminal
        scrollback can't be physically erased, so we instead emit a blank-
        line separator before the replacement chunk so the reader can tell
        the prior partial block ended and a new block began — without the
        separator the two cumulative texts mash together onto the same line
        and look like duplicate output.
        """
        if not self.enabled:
            return

        delta = str(text or "")
        if not delta:
            return

        stream_id = (
            assistant_id.strip()
            if isinstance(assistant_id, str) and assistant_id.strip()
            else "assistant"
        )

        with self._terminal_lock:
            previously_streaming = self._assistant_stream_live_suspended
            if not self._assistant_stream_live_suspended:
                self._progress_manager.suspend_live()
                self._assistant_stream_live_suspended = True

            if isinstance(full_text, str):
                self._assistant_stream_text_by_id[stream_id] = full_text
            else:
                previous = self._assistant_stream_text_by_id.get(stream_id, "")
                self._assistant_stream_text_by_id[stream_id] = previous + delta

            if replace_content and previously_streaming:
                self.console.print(
                    "",
                    end="\n",
                    highlight=False,
                    soft_wrap=True,
                )
            self.console.print(
                delta,
                end="",
                highlight=False,
                soft_wrap=True,
            )

    @override
    def report_status_message(
        self,
        message: str,
        *,
        assistant_id: str | None = None,
    ) -> None:
        """Render a standalone status message."""
        _ = assistant_id
        if not self.enabled:
            return

        with self._terminal_lock:
            if self._assistant_stream_live_suspended:
                self.console.print()
                self._assistant_stream_live_suspended = False
                self._progress_manager.resume_live()

            self._display_manager.print_event("STATUS", message)


# MARK: Final Output Helpers


class RichReporterFinalOutputMixin(BaseReporterInterface, ABC):
    if TYPE_CHECKING:
        enabled: bool
        _terminal_lock: AbstractContextManager[bool]
        _progress_manager: ProgressManager
        _display_manager: DisplayManager
        _assistant_stream_text_by_id: dict[str, str]
        _assistant_stream_live_suspended: bool
        console: Console

    @override
    def print_final_response(self, response: str) -> None:
        """Print final assistant response text."""
        if not self.enabled:
            return

        extracted = extract_text_from_response(response)
        response_text = extracted.strip() if isinstance(extracted, str) else response.strip()

        with self._terminal_lock:
            if self._has_matching_streamed_response(response_text):
                self.console.print()
                self._clear_assistant_stream_state()
                return

            if self._assistant_stream_text_by_id:
                self.console.print()
            self._display_manager.print_final_response(response)
            self._clear_assistant_stream_state()

    def _has_matching_streamed_response(self, response_text: str) -> bool:
        target = response_text.strip()
        if not target or not self._assistant_stream_text_by_id:
            return False
        return any(text.strip() == target for text in self._assistant_stream_text_by_id.values())

    def _clear_assistant_stream_state(self) -> None:
        self._assistant_stream_text_by_id.clear()
        if self._assistant_stream_live_suspended:
            self._progress_manager.resume_live()
            self._assistant_stream_live_suspended = False
