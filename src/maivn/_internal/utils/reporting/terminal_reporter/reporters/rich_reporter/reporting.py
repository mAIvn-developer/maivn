"""Event and tool reporting for ``RichReporter``.
Formats tool execution events and results for display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.markup import escape
from rich.text import Text

from ..._components import EventTracker
from ..._formatters import (
    format_elapsed_time,
    format_session_id,
    get_event_color,
    get_event_icon,
)
from .._shared_helpers import (
    build_error_details,
    build_tool_start_details,
    get_tool_prefix,
    is_reevaluate_system_tool,
)
from .streaming_handler import StreamingHandler
from .streaming_progress import report_system_tool_progress
from .tool_children import ToolChildRenderer

if TYPE_CHECKING:
    from rich.console import Console


# MARK: Tool Reporter


class ToolReporter:
    """Handles tool execution reporting."""

    STREAMING_MAX_LINES = 50

    # MARK: - Initialization

    def __init__(self, console: Console) -> None:
        """Initialize tool reporter."""
        self.console = console
        self.tracker = EventTracker()

        self._child_renderer = ToolChildRenderer(
            print_to_console=self._print_to_console,
        )
        self._streaming = StreamingHandler(console)

    # MARK: - Public Reporting Methods

    def report_session_start(
        self,
        session_id: str,
        assistant_id: str,
    ) -> None:
        """Report session start."""
        self._print_event(
            "info",
            "Session Started",
            {
                "Session ID": format_session_id(session_id),
                "Assistant": assistant_id,
            },
        )

    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        """Report tool execution start."""
        self.tracker.record_tool_start(
            tool_name,
            event_id,
            tool_type,
            agent_name,
            tool_args,
        )

        if is_reevaluate_system_tool(tool_name, tool_type):
            return

        if self.tracker.tools_executed > 1:
            self.console.print()

        details = build_tool_start_details(
            event_id,
            tool_type,
            agent_name,
            tool_args,
        )
        self._print_event("tool", f"Executing: {tool_name}", details)

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
    ) -> None:
        """Report tool execution completion."""
        tool_info = self.tracker.get_tool_info(event_id)
        if not tool_info:
            return

        if elapsed_ms is None:
            elapsed_ms = self.tracker.calculate_elapsed_ms(event_id)

        tool_name = str(tool_info.get("name") or "")
        tool_type = str(tool_info.get("tool_type") or "")

        if is_reevaluate_system_tool(tool_name, tool_type):
            self._print_reevaluate_message()
            return

        self._print_tool_completion(tool_info, elapsed_ms)
        self._child_renderer.print_tool_children(tool_info, result)

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        *,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Report tool execution error."""
        details = build_error_details(error, event_id, elapsed_ms)
        self._print_event("error", f"Failed: {tool_name}", details)

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: Any | None = None,
    ) -> None:
        """Report MODEL tool execution completion."""
        _ = (event_id, swarm_name)
        self.tracker.record_model_tool()

        text = Text()
        text.append("[MODEL] ", style="bold magenta")
        text.append(f"[OK] Complete: {tool_name}", style="green")
        self._print_to_console(text)
        if result is not None:
            tool_info: dict[str, Any] = {
                "name": tool_name,
                "tool_type": "model",
            }
            if agent_name:
                tool_info["agent_name"] = agent_name
            self._child_renderer.print_tool_children(tool_info, result)

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text_content: str | None = None,
        *,
        stream_group_event_ids: set[str] | None = None,
    ) -> None:
        """Report system tool execution progress."""
        normalized_tool = str(tool_name or "").strip().lower()
        wants_panel = normalized_tool in {"think", "repl"}

        supports_in_place = self._streaming.supports_in_place_updates()
        display_text = self._streaming.get_display_text(
            event_id,
            chunk_count,
            text_content,
        )

        if wants_panel and supports_in_place:
            self._streaming.handle_panel_progress(
                event_id=event_id,
                tool_name=tool_name,
                elapsed_seconds=elapsed_seconds,
                chunk_count=chunk_count,
                text_content=display_text or "",
                stream_group_event_ids=stream_group_event_ids,
            )
            return

        report_system_tool_progress(
            console=self.console,
            print_to_console=self._print_to_console,
            event_id=event_id,
            tool_name=tool_name,
            streaming_max_lines=self.STREAMING_MAX_LINES,
            previous_event_id=None,
            previous_lines_printed=0,
            elapsed_seconds=elapsed_seconds,
            text_content=display_text,
        )

    def clear_streaming_state(self) -> None:
        """Clear streaming state. Call when tool execution completes."""
        self._streaming.clear_state()

    def finalize_system_tool_stream_group(self) -> None:
        """Finalize the current in-place streaming region."""
        self._streaming.finalize_stream_group()

    # MARK: - Output Methods

    def _print_to_console(self, content: str | Text) -> None:
        """Print content to console with standard formatting."""
        self.console.print(content, overflow="fold")

    def _print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Print an event with color coding."""
        color = get_event_color(event_type)
        icon = get_event_icon(event_type)

        self._print_to_console(f"[{color}]{icon} {message}[/{color}]")

        if details:
            self._print_event_details(details, color)

    def _print_event_details(
        self,
        details: dict[str, Any],
        color: str,
    ) -> None:
        """Print event details with proper formatting."""
        for key, value in details.items():
            value_str = "" if value is None else str(value)
            self._print_detail_line(key, value_str, color)

    def _print_detail_line(
        self,
        key: str,
        value_str: str,
        color: str,
    ) -> None:
        """Print a single detail line, handling multiline values."""
        if "\n" in value_str:
            self._print_to_console(
                f"  [{color}dim]{key}:[/{color}dim]",
            )
            indented = "\n".join(
                f"    [dim]{escape(line)}[/dim]" for line in value_str.splitlines()
            )
            self._print_to_console(indented)
        else:
            self._print_to_console(
                f"  [{color}dim]{key}:[/{color}dim] [dim]{escape(value_str)}[/dim]",
            )

    # MARK: - Tool Completion Formatting

    def _print_tool_completion(
        self,
        tool_info: dict[str, Any],
        elapsed_ms: int,
    ) -> None:
        """Print tool completion message."""
        prefix = get_tool_prefix(tool_info.get("tool_type"))
        elapsed_str = format_elapsed_time(elapsed_ms)

        prefix_styles = {
            "AGENT": "bold magenta",
            "MODEL": "bold yellow",
            "SYSTEM": "bold blue",
            "MCP": "bold green",
            "FUNC": "bold cyan",
        }
        style = prefix_styles.get(prefix, "bold cyan")

        text = Text()
        text.append(f"[{prefix}] ", style=style)
        text.append(
            f"[OK] Completed: {tool_info['name']} ({elapsed_str})",
            style="green",
        )
        self._print_to_console(text)

    def _print_reevaluate_message(self) -> None:
        """Print reevaluate system tool message."""
        text = Text()
        text.append("[SYSTEM] ", style="bold blue")
        text.append("[OK] Reevaluating", style="green")
        self._print_to_console(text)
