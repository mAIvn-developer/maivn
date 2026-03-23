"""Tool child result rendering for RichReporter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.text import Text

from ..._formatters import result_to_json
from ...config import RESULT_LABEL_STYLE, RESULT_VALUE_STYLE
from ..reporter_base import (
    collect_injected_data_info,
    extract_response_text,
    extract_result_for_display,
)

# MARK: - Constants

# Rich style mapping for injected data labels
_INJECTED_DATA_STYLES: dict[str, str] = {
    "private_data_injected": "bold yellow",
    "interrupt_data_injected": "bold magenta",
}


# MARK: - ToolChildRenderer


class ToolChildRenderer:
    """Renders tool execution child items (agent, injected data, response, result)."""

    def __init__(self, *, print_to_console: Callable[[str | Text], None]) -> None:
        self._print_to_console = print_to_console

    def print_tool_children(self, tool_info: dict[str, Any], result: Any | None) -> None:
        """Print all child items for a tool completion."""
        child_items = self._collect_child_items(tool_info, result)

        for idx, printer in enumerate(child_items):
            connector = "'--" if idx == len(child_items) - 1 else "|--"
            printer(f"  {connector} ")

    # MARK: - Child Collection

    def _collect_child_items(
        self, tool_info: dict[str, Any], result: Any | None
    ) -> list[Callable[[str], None]]:
        """Collect all child items to render."""
        child_items: list[Callable[[str], None]] = []

        # Agent name
        agent_name = tool_info.get("agent_name")
        if agent_name:
            child_items.append(
                self._create_line_printer("Agent", str(agent_name), "bold magenta", "cyan")
            )

        # Injected data
        child_items.extend(self._get_injected_data_printers(result))

        # Response text
        response_printer = self._get_response_printer(result)
        if response_printer:
            child_items.append(response_printer)

        # Result
        result_printer = self._get_result_printer(tool_info, result)
        if result_printer:
            child_items.append(result_printer)

        return child_items

    def _get_injected_data_printers(self, result: Any) -> list[Callable[[str], None]]:
        """Get printers for injected data items."""
        items = collect_injected_data_info(result)
        printers: list[Callable[[str], None]] = []

        for key, label, keys in items:
            style = _INJECTED_DATA_STYLES.get(key, "bold yellow")
            printers.append(self._create_line_printer(label, ", ".join(keys), style, "cyan"))

        return printers

    def _get_response_printer(self, result: Any) -> Callable[[str], None] | None:
        """Get printer for response text if present."""
        response_text = extract_response_text(result)
        if not response_text:
            return None

        formatted = _format_value(response_text, max_lines=25, max_chars=8000)
        return self._create_line_printer(
            "Response", formatted, RESULT_LABEL_STYLE, RESULT_VALUE_STYLE
        )

    def _get_result_printer(
        self, tool_info: dict[str, Any], result: Any
    ) -> Callable[[str], None] | None:
        """Get printer for result value if present."""
        result_to_display = extract_result_for_display(result, tool_info)
        if result_to_display is None:
            return None

        formatted = _format_value(result_to_display, max_lines=25, max_chars=8000)
        return self._create_line_printer(
            "Result", formatted, RESULT_LABEL_STYLE, RESULT_VALUE_STYLE
        )

    # MARK: - Printing Helpers

    def _create_line_printer(
        self, label: str, value: str, label_style: str, value_style: str
    ) -> Callable[[str], None]:
        """Create a function that prints a labeled line."""

        def printer(prefix: str) -> None:
            self._print_child_line(prefix, label, value, label_style, value_style)

        return printer

    def _print_child_line(
        self, prefix: str, label: str, value: str, label_style: str, value_style: str
    ) -> None:
        """Print a single child line with optional multiline continuation."""
        label_style_dim = f"{label_style} dim"
        value_style_dim = f"{value_style} dim"

        lines = value.splitlines() if value else [""]
        first = lines[0] if lines else ""

        text = Text()
        text.append(prefix, style="dim")
        text.append(f"{label}: ", style=label_style_dim)
        text.append(first, style=value_style_dim)
        self._print_to_console(text)

        if len(lines) > 1:
            continuation_prefix = " " * len(prefix) + " " * (len(label) + 2)
            for line in lines[1:]:
                cont = Text()
                cont.append(continuation_prefix, style="dim")
                cont.append(line, style=value_style_dim)
                self._print_to_console(cont)


# MARK: - Formatting Helper


def _format_value(value: Any, *, max_lines: int, max_chars: int) -> str:
    """Format a value for display with line and character limits."""
    if value is None:
        return ""

    # Convert to string
    if isinstance(value, (dict, list, tuple)):
        try:
            formatted = result_to_json(value)
        except (TypeError, ValueError, RecursionError):
            formatted = str(value)
    else:
        formatted = str(value)

    if not formatted:
        return formatted

    lines = formatted.splitlines() if formatted else []

    # Apply line limit
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[:max_lines] + ["..."]

    # Apply character limit
    if max_chars > 0:
        limited: list[str] = []
        total = 0
        for line in lines:
            next_total = total + len(line) + 1
            if next_total > max_chars:
                limited.append("...")
                break
            limited.append(line)
            total = next_total
        lines = limited

    return "\n".join(lines)
