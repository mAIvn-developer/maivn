"""Assistant streaming methods for ``SimpleReporter``."""

from __future__ import annotations

from typing import Any

# MARK: Assistant Streaming


class SimpleReporterAssistantStreamingMixin:
    enabled: bool
    _assistant_stream_text_by_id: dict[str, str]
    _assistant_stream_active: bool
    print_event: Any

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

        normalized_assistant_id = assistant_id.strip() if isinstance(assistant_id, str) else ""
        stream_id = normalized_assistant_id or "assistant"
        if isinstance(full_text, str):
            self._assistant_stream_text_by_id[stream_id] = full_text
        else:
            previous = self._assistant_stream_text_by_id.get(stream_id, "")
            self._assistant_stream_text_by_id[stream_id] = previous + delta

        self._assistant_stream_active = True
        print(delta, end="", flush=True)

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

        if self._assistant_stream_active:
            print()
            self._assistant_stream_active = False

        self.print_event("STATUS", message)

    def _has_matching_streamed_response(self, response_text: str) -> bool:
        target = response_text.strip()
        if not target or not self._assistant_stream_text_by_id:
            return False
        return any(text.strip() == target for text in self._assistant_stream_text_by_id.values())

    def _clear_assistant_stream_state(self) -> None:
        self._assistant_stream_text_by_id.clear()
        self._assistant_stream_active = False
