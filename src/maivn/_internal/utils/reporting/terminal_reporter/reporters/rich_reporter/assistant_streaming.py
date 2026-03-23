"""Assistant streaming methods for ``RichReporter``."""

from __future__ import annotations

from typing import Any

from ..._formatters import extract_text_from_response

# MARK: Assistant Streaming


class RichReporterAssistantStreamingMixin:
    enabled: bool
    _terminal_lock: Any
    _progress_manager: Any
    _display_manager: Any
    _assistant_stream_text_by_id: dict[str, str]
    _assistant_stream_live_suspended: bool
    console: Any

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
    ) -> None:
        """Render incremental assistant response text."""
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
            if not self._assistant_stream_live_suspended:
                self._progress_manager.suspend_live()
                self._assistant_stream_live_suspended = True

            if isinstance(full_text, str):
                self._assistant_stream_text_by_id[stream_id] = full_text
            else:
                previous = self._assistant_stream_text_by_id.get(stream_id, "")
                self._assistant_stream_text_by_id[stream_id] = previous + delta

            self.console.print(
                delta,
                end="",
                highlight=False,
                soft_wrap=True,
            )

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


class RichReporterFinalOutputMixin:
    enabled: bool
    _terminal_lock: Any
    _progress_manager: Any
    _display_manager: Any
    _assistant_stream_text_by_id: dict[str, str]
    _assistant_stream_live_suspended: bool
    console: Any

    def print_final_response(self, response: str) -> None:
        """Print final assistant response text."""
        if not self.enabled:
            return

        extracted = extract_text_from_response(response)
        if isinstance(extracted, str):
            response_text = extracted.strip()
        elif isinstance(response, str):
            response_text = response.strip()
        else:
            response_text = str(response)

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
