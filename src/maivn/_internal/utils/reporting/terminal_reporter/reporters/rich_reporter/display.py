"""Display and output helpers for RichReporter.
Provides rich panels, tables, and formatting for console output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maivn_shared.utils.token_models import TokenUsage
from rich import box
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..._components import FileWriter
from ..._formatters import (
    count_lines,
    extract_text_from_response,
    format_file_size,
    format_total_time,
    get_event_color,
    get_event_icon,
    result_to_json,
)
from ...config import (
    ERROR_BORDER_STYLE,
    HEADER_BORDER_STYLE,
    HEADER_PADDING,
    MAX_INLINE_RESULT_LENGTH,
    MAX_RESULT_LINES,
    RESULT_PADDING,
    SECTION_BORDER_STYLE,
    SECTION_PADDING,
    SUMMARY_BORDER_STYLE,
)

if TYPE_CHECKING:
    from rich.console import Console

    from ..._components import EventTracker


# MARK: Display Manager


class DisplayManager:
    """Handles display and output formatting."""

    def __init__(self, console: Console, tracker: EventTracker) -> None:
        """Initialize display manager.

        Args:
            console: Rich console instance
            tracker: Event tracker for metrics
        """
        self.console = console
        self.tracker = tracker
        self.file_writer = FileWriter()

    def _print(self, *args: Any, **kwargs: Any) -> None:
        """Print with default overflow settings.

        Args:
            *args: Positional arguments for console.print
            **kwargs: Keyword arguments for console.print
        """
        # Don't use soft_wrap as it causes mid-word breaks at console width
        kwargs.setdefault("overflow", "fold")
        self.console.print(*args, **kwargs)

    def print_header(self, title: str, subtitle: str = "") -> None:
        """Print a beautiful header.

        Args:
            title: Main title
            subtitle: Optional subtitle
        """
        text = Text()
        text.append(f"\n{title}\n", style="bold cyan")
        if subtitle:
            text.append(f"{subtitle}\n", style="dim")

        panel = Panel(
            text,
            border_style=HEADER_BORDER_STYLE,
            padding=HEADER_PADDING,
        )
        self._print(panel)

    def print_section(self, title: str, style: str = "bold cyan") -> None:
        """Print a section header.

        Args:
            title: Section title
            style: Rich style string
        """
        panel = Panel(
            Text(title, justify="center", style=style),
            border_style=SECTION_BORDER_STYLE,
            padding=SECTION_PADDING,
            box=box.SIMPLE,
        )
        self._print()
        self._print(panel)

    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Print an event with color coding.

        Args:
            event_type: Type of event (info, success, warning, error)
            message: Event message
            details: Optional details dictionary
        """
        color = get_event_color(event_type)
        icon = get_event_icon(event_type)

        self._print(f"[{color}]{icon} {message}[/{color}]")

        if details:
            for key, value in details.items():
                self._print(f"  [{color}dim]{key}:[/{color}dim] {value}")

    def print_private_data(self, private_data: dict[str, Any]) -> None:
        """Print private data parameters.

        Args:
            private_data: Dictionary of private data parameters
        """
        if not private_data:
            return

        table = Table(title="[bold cyan]Private Data Parameters[/bold cyan]", border_style="cyan")
        table.add_column("Parameter", style="yellow", no_wrap=True)
        table.add_column("Value", style="green", overflow="fold")

        for key, value in private_data.items():
            table.add_row(key, f"[REDACTED] ({type(value).__name__})")

        self._print()
        self._print(table)

    def print_phase_change(self, phase: str) -> None:
        """Print phase change.

        Args:
            phase: New phase name
        """
        self.tracker.set_phase(phase)
        self.print_section(f"Phase: {phase}", "bold yellow")

    def print_summary(self, token_usage: TokenUsage | None = None) -> None:
        """Print execution summary.

        Args:
            token_usage: Optional token usage data from the session.
        """
        metrics = self.tracker.get_summary_metrics()

        table = Table(title="Execution Summary", border_style=SUMMARY_BORDER_STYLE)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        table.add_row("Tools Executed", str(metrics["tools_executed"]))
        table.add_row("Total Time", format_total_time(metrics["elapsed_seconds"]))

        # Add token usage metrics if available
        if token_usage and token_usage.total_tokens > 0:
            table.add_row("", "")  # Separator row
            table.add_row("[bold]Token Usage[/bold]", "")
            table.add_row("  Total Tokens", f"{token_usage.total_tokens:,}")
            table.add_row("  Input Tokens", f"{token_usage.input_tokens:,}")
            table.add_row("  Output Tokens", f"{token_usage.output_tokens:,}")
            if getattr(token_usage, "reasoning_tokens", 0) > 0:
                table.add_row("  Reasoning Tokens", f"{token_usage.reasoning_tokens:,}")
            if token_usage.cache_read_tokens > 0:
                table.add_row("  Cache Read", f"{token_usage.cache_read_tokens:,}")
            if token_usage.cache_creation_tokens > 0:
                table.add_row("  Cache Created", f"{token_usage.cache_creation_tokens:,}")

        self._print("\n")
        self._print(table)
        self._print("\n")

    def print_final_result(self, result: Any) -> None:
        """Print final result in a copyable format.

        Args:
            result: The final result to display
        """
        self._print()
        self._print(
            Rule("[bold magenta]Final Result[/bold magenta]", style="bold magenta", characters="=")
        )
        self._print()

        try:
            result_json = result_to_json(result)
            line_count = count_lines(result_json)

            max_line_len = max((len(line) for line in result_json.splitlines()), default=0)
            # Only trigger file write for extremely long lines that would cause rendering issues
            has_extremely_long_lines = max_line_len > 1000

            if (
                self.file_writer.should_write_to_file(
                    result_json,
                    max_lines=MAX_RESULT_LINES,
                )
                or has_extremely_long_lines
            ):
                file_path, file_size = self.file_writer.write_result(result_json, "json")

                if has_extremely_long_lines:
                    self._print(
                        "[yellow]Result contains long lines that may be cut off "
                        "by the terminal renderer[/yellow]"
                    )
                else:
                    too_large_msg = (
                        "[yellow]Result too large for terminal display ("
                        f"{line_count} lines)[/yellow]"
                    )
                    self._print(too_large_msg)
                self._print(f"[green][OK][/green] Written to: [cyan]{file_path}[/cyan]")
                self._print(f"[dim]File size: {format_file_size(file_size)}[/dim]")
            else:
                syntax = Syntax(
                    result_json,
                    "json",
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                )
                self._print(syntax)
        except (TypeError, ValueError, RecursionError):
            result_str = str(result)

            if self.file_writer.should_write_to_file(
                result_str,
                max_chars=MAX_INLINE_RESULT_LENGTH,
            ):
                file_path, file_size = self.file_writer.write_result(result_str, "txt")

                self._print("[yellow]Result too large for terminal display[/yellow]")
                self._print(f"[green][OK][/green] Written to: [cyan]{file_path}[/cyan]")
                self._print(f"[dim]File size: {format_file_size(file_size)}[/dim]")
            else:
                self._print(result_str)

        self._print()
        self._print()

    def print_final_response(self, response: str) -> None:
        """Print final assistant response text."""
        self._print()
        self._print(
            Rule(
                "[bold magenta]Final Response[/bold magenta]",
                style="bold magenta",
                characters="=",
            )
        )
        self._print()

        extracted_text = extract_text_from_response(response)
        response_text = (
            extracted_text.strip()
            if isinstance(extracted_text, str)
            else response.strip()
            if isinstance(response, str)
            else str(response)
        )
        if not response_text:
            self._print("[dim](no response text)[/dim]")
            self._print()
            self._print()
            return

        if self.file_writer.should_write_to_file(response_text, max_chars=MAX_INLINE_RESULT_LENGTH):
            file_path, file_size = self.file_writer.write_result(response_text, "txt")
            self._print("[yellow]Response too large for terminal display[/yellow]")
            self._print(f"[green][OK][/green] Written to: [cyan]{file_path}[/cyan]")
            self._print(f"[dim]File size: {format_file_size(file_size)}[/dim]")
            self._print()
            self._print()
            return

        # Render as Markdown for nice formatting
        md = Markdown(response_text)
        self.console.print(md)
        self._print()
        self._print()

    def print_error_summary(self, error: str) -> None:
        """Print error summary.

        Args:
            error: Error message
        """
        panel = Panel(
            f"[red bold]Error:[/red bold] {error}",
            border_style=ERROR_BORDER_STYLE,
            padding=RESULT_PADDING,
        )
        self._print("\n")
        self._print(panel)
        self._print("\n")
