"""Rich-backed terminal output (Tier 1): the same content as Tier 0, styled.

Every line's content comes from :class:`SimpleReporter`; this class only maps
semantic tones onto color and swaps bordered blocks for panels. Colors follow
the Studio state semantics from maivn-design - signal/cyan for activity,
nominal/green for success, alert/red for failure, caution/yellow for warnings -
expressed as plain ANSI names so the user's terminal palette keeps them legible
on both light and dark themes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import (
    GUTTER_WIDTH,
    SimpleReporter,
    display_response,
    token_usage_lines,
)

if TYPE_CHECKING:
    from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import EventLine

_TONE_STYLES: Final[dict[str, str]] = {
    'active': 'cyan',
    'success': 'green',
    'error': 'red',
    'dim': 'dim',
    'info': 'cyan',
}
"""Semantic tone to ANSI style, mirroring Studio's signal/nominal/alert tokens."""


class RichReporter(SimpleReporter):
    """Restyle the shared reporter content with color, panels, and soft wrapping."""

    def __init__(self, *, enabled: bool = True, force_terminal: bool | None = None) -> None:
        """Initialize Rich output while retaining the v2 reporter callback surface.

        ``force_terminal`` keeps styling on when output is captured (the
        explicit rich-tier override); ``None`` lets Rich autodetect. Automatic
        highlighting stays off so numbers and strings in model output are not
        sprayed with colors the design language never chose.
        """
        super().__init__(enabled=enabled)
        if force_terminal:
            # Forced rendering targets captures and pipes. Windows console
            # autodetection (legacy mode, or a TERM-less PowerShell) routes
            # color through Win32 calls and emits no ANSI bytes, so pin plain
            # 16-color SGR - the only palette this reporter uses anyway.
            self.console = Console(
                highlight=False,
                force_terminal=True,
                legacy_windows=False,
                color_system='standard',
            )
        else:
            self.console = Console(highlight=False)

    def _render_event_line(self, line: EventLine) -> None:
        """Print one aligned event line with its tone color on the verb gutter."""
        self._break_stream()
        text = Text()
        text.append(line.verb.rjust(GUTTER_WIDTH), style=_TONE_STYLES.get(line.tone, 'cyan'))
        if line.tone == 'error':
            text.append(f' {line.text}', style='red')
        else:
            text.append(f' {line.text}')
        if line.detail:
            text.append(f'  {line.detail}', style='dim')
        self.console.print(text)

    def _render_stream_text(self, text: str) -> None:
        """Print streamed content without hard-wrapping partial deltas."""
        self.console.print(Text(text), end='', soft_wrap=True)
        self._stream_open = not text.endswith('\n')

    def _break_stream(self) -> None:
        """Terminate an in-flight streamed line through the Rich console."""
        if self._stream_open:
            self.console.print()
            self._stream_open = False

    def print_final_response(self, response: str) -> None:
        """Render the final response once, avoiding a duplicate streamed transcript."""
        if not self.enabled:
            return
        if any(text == response for text in self._streamed_text_by_assistant.values()):
            self.console.print()
            self._stream_open = False
        else:
            self._break_stream()
            rendered = Text(display_response(response))
            self.console.print(Panel(rendered, title='FINAL RESPONSE', border_style='green'))
        self._streamed_text_by_assistant.clear()

    def print_summary(self, token_usage: object | None = None) -> None:
        """Render stable tool, duration, and token telemetry in a panel."""
        if not self.enabled:
            return
        self._break_stream()
        lines = [
            f'Tools Executed: {self._tool_count}',
            f'Total Time: {self._elapsed_seconds():.2f}s',
        ]
        token_lines = token_usage_lines(token_usage)
        if token_lines:
            lines.extend(['', 'TOKEN USAGE', *token_lines])
        self.console.print(
            Panel(Text('\n'.join(lines)), title='EXECUTION SUMMARY', border_style='dim')
        )

    def print_final_result(self, result: object) -> None:
        """Render a structured result when the event stream provides one."""
        if not self.enabled or result is None:
            return
        self._break_stream()
        rendered = display_response(result) if isinstance(result, str) else str(result)
        self.console.print(Panel(Text(rendered), title='RESULT', border_style='green'))

    def get_input(  # noqa: PLR0913 - preserves the reporter compatibility surface.
        self,
        prompt: str,
        *,
        input_type: str = 'text',
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
        tool_name: str | None = None,
    ) -> str:
        """Collect terminal input without narrowing dependency-interrupt metadata."""
        _ = (input_type, choices, data_key, arg_name, tool_name)
        if not self.enabled:
            return input(prompt)
        self._break_stream()
        return self.console.input(_prompt_for_terminal_input(prompt))


def _prompt_for_terminal_input(prompt: str) -> str:
    """Add the separator Rich does not insert between a prompt and user input."""
    return prompt if prompt[-1:].isspace() else f'{prompt} '


__all__ = ['RichReporter']
