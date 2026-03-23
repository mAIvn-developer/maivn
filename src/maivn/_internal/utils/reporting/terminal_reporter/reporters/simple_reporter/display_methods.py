"""Display and summary methods for ``SimpleReporter``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..._formatters import format_total_time
from ...config import (
    MAX_INLINE_RESULT_LENGTH,
    SIMPLE_BORDER_LENGTH,
)
from .display_helpers import (
    create_simple_border,
    normalize_response_text,
    print_or_write_result,
    print_result_content,
)

if TYPE_CHECKING:
    from maivn_shared.utils.token_models import TokenUsage


# MARK: Display Methods


class SimpleReporterDisplayMixin:
    enabled: bool
    tracker: Any
    file_writer: Any
    _assistant_stream_active: bool
    _assistant_stream_text_by_id: dict[str, str]
    _border_char: str
    _clear_assistant_stream_state: Any
    _has_matching_streamed_response: Any

    def print_header(self, title: str, subtitle: str = "") -> None:
        """Print a header."""
        if not self.enabled:
            return

        border = self._make_border()
        print(f"\n{border}")
        print(f"{title}")
        if subtitle:
            print(f"{subtitle}")
        print(f"{border}\n")

    def print_section(self, title: str, style: str = "") -> None:
        """Print a section header."""
        _ = style
        if not self.enabled:
            return

        from ...config import SIMPLE_BOX_CORNERS, SIMPLE_BOX_HORIZONTAL, SIMPLE_BOX_VERTICAL
        from .display_helpers import print_section_box

        print_section_box(
            title=title,
            horizontal_char=SIMPLE_BOX_HORIZONTAL,
            vertical_char=SIMPLE_BOX_VERTICAL,
            corners=SIMPLE_BOX_CORNERS,
        )

    def print_summary(self, token_usage: TokenUsage | None = None) -> None:
        """Print execution summary."""
        if not self.enabled:
            return

        metrics = self.tracker.get_summary_metrics()
        border = self._make_border()

        print(f"\n{border}")
        print("EXECUTION SUMMARY")
        print(border)
        print(f"Tools Executed: {metrics['tools_executed']}")
        print(f"Total Time: {format_total_time(metrics['elapsed_seconds'])}")

        if token_usage and token_usage.total_tokens > 0:
            print(border)
            print("TOKEN USAGE")
            print(f"  Total Tokens: {token_usage.total_tokens:,}")
            print(f"  Input Tokens: {token_usage.input_tokens:,}")
            print(f"  Output Tokens: {token_usage.output_tokens:,}")
            if getattr(token_usage, "reasoning_tokens", 0) > 0:
                print(f"  Reasoning Tokens: {token_usage.reasoning_tokens:,}")
            if token_usage.cache_read_tokens > 0:
                print(f"  Cache Read: {token_usage.cache_read_tokens:,}")
            if token_usage.cache_creation_tokens > 0:
                print(f"  Cache Created: {token_usage.cache_creation_tokens:,}")

        print(f"{border}\n")

    def print_final_result(self, result: Any) -> None:
        """Print final result."""
        if not self.enabled:
            return

        self._clear_assistant_stream_state()
        border = create_simple_border()
        print(f"\n{border}")
        print("FINAL RESULT")
        print(border)

        print_result_content(result, self.file_writer)

        print(f"{border}\n")

    def print_final_response(self, response: str) -> None:
        """Print final assistant response text."""
        if not self.enabled:
            return

        from ..._formatters import extract_text_from_response

        extracted_text = extract_text_from_response(response)
        response_text = normalize_response_text(extracted_text, response)
        if self._has_matching_streamed_response(response_text):
            print()
            self._clear_assistant_stream_state()
            return

        if self._assistant_stream_active:
            print()

        border = create_simple_border()
        print(f"\n{border}")
        print("FINAL RESPONSE")
        print(border)

        print_or_write_result(
            response_text,
            "txt",
            None,
            MAX_INLINE_RESULT_LENGTH,
            self.file_writer,
        )

        print(f"{border}\n")
        self._clear_assistant_stream_state()

    def print_error_summary(self, error: str) -> None:
        """Print error summary."""
        if not self.enabled:
            return

        self._clear_assistant_stream_state()
        border = self._make_border()
        print(f"\n{border}")
        print(f"ERROR: {error}")
        print(f"{border}\n")

    def _make_border(self, length: int = SIMPLE_BORDER_LENGTH) -> str:
        return self._border_char * length
