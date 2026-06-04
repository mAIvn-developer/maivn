"""Assistant streaming methods for ``SimpleReporter``."""

# pyright: strict

from __future__ import annotations

from abc import ABC, abstractmethod

from typing_extensions import override

from ...base.defaults import ReporterDefaultEventsMixin

# MARK: Assistant Streaming


class SimpleReporterAssistantStreamingMixin(ReporterDefaultEventsMixin, ABC):
    enabled: bool
    _assistant_stream_text_by_id: dict[str, str]
    _assistant_stream_active: bool

    @abstractmethod
    @override
    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None: ...

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

        ``replace_content`` signals that this chunk represents a fresh
        synthesis (reevaluate cycle, evaluate re-entry, mid-stream revision)
        and the bubble should be overwritten with the full new cumulative
        text. The terminal can't physically erase prior scrollback, so we
        emit a visible separator (blank line) before the replacement so the
        reader can distinguish the new content from the prior block instead
        of seeing them mash together.
        """
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

        if replace_content and self._assistant_stream_active:
            # Visual separator: blank line, then the full new text. Without
            # this the terminal shows the prior partial cumulative text
            # immediately followed by the new cumulative text on the same
            # line, producing the "chopping" / duplicate-block effect.
            print("\n", end="", flush=True)
        self._assistant_stream_active = True
        print(delta, end="", flush=True)

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

        if self._assistant_stream_active:
            print()
            self._assistant_stream_active = False

        self.print_event("STATUS", message)

    def _has_matching_streamed_response(
        self,
        response_text: str,
    ) -> bool:
        target = response_text.strip()
        if not target or not self._assistant_stream_text_by_id:
            return False
        return any(text.strip() == target for text in self._assistant_stream_text_by_id.values())

    def _clear_assistant_stream_state(self) -> None:
        self._assistant_stream_text_by_id.clear()
        self._assistant_stream_active = False
