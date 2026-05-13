"""Display helper functions for SimpleReporter.
Box drawing, result printing, and event label formatting.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..._components import FileWriter
from ..._formatters import (
    format_file_size,
    result_to_json,
    truncate_result,
)
from ...config import (
    MAX_INLINE_RESULT_LENGTH,
    MAX_RESULT_LINES,
    SIMPLE_BORDER_CHAR,
    SIMPLE_BORDER_LENGTH,
    SIMPLE_BOX_CORNERS,
    SIMPLE_BOX_HORIZONTAL,
    SIMPLE_BOX_VERTICAL,
)
from .._shared_helpers import extract_response_text

# MARK: Box Drawing


def create_border(*, border_char: str, length: int) -> str:
    """Create a repeated character border."""
    return border_char * length


def create_simple_border(length: int = SIMPLE_BORDER_LENGTH) -> str:
    """Create a standard simple border string."""
    return SIMPLE_BORDER_CHAR * length


def print_boxed_header(
    *,
    title: str,
    width: int,
    horizontal_char: str,
    vertical_char: str,
    corners: tuple[str, str, str, str],
) -> None:
    """Print a boxed header row."""
    border = horizontal_char * width
    tl, tr, _bl, _br = corners
    padding = width - len(title) - 2

    print(f"\n{tl}{border}{tr}")
    print(f"{vertical_char}  {title}{' ' * padding}{vertical_char}")
    print(f"{tl}{border}{tr}")


def print_boxed_row(*, content: str, width: int, vertical_char: str) -> None:
    """Print a single boxed row."""
    padding = max(0, width - len(content))
    print(f"{vertical_char}{content}{' ' * padding}{vertical_char}")


def print_boxed_footer(
    *, width: int, horizontal_char: str, corners: tuple[str, str, str, str]
) -> None:
    """Print a boxed footer row."""
    border = horizontal_char * width
    _tl, _tr, bl, br = corners
    print(f"{bl}{border}{br}\n")


def print_section_box(
    *,
    title: str,
    horizontal_char: str = SIMPLE_BOX_HORIZONTAL,
    vertical_char: str = SIMPLE_BOX_VERTICAL,
    corners: tuple[str, str, str, str] = SIMPLE_BOX_CORNERS,
) -> None:
    """Print a section title in a box."""
    border = horizontal_char * (len(title) + 4)
    tl, tr, bl, br = corners

    print(f"\n{tl}{border}{tr}")
    print(f"{vertical_char}  {title}  {vertical_char}")
    print(f"{bl}{border}{br}\n")


def print_kv_lines(*, details: dict[str, Any]) -> None:
    """Print key-value detail lines."""
    for key, value in details.items():
        print(f"  {key}: {value}")


# MARK: Tool Output


def print_tool_child_lines(
    *,
    result: Any,
    truncate_fn: Callable[[str], str] = truncate_result,
) -> None:
    """Print tool child result lines with tree-style connectors."""
    lines: list[str] = []

    if isinstance(result, dict):
        private_data_injected = result.get("private_data_injected", [])
        if private_data_injected:
            lines.append(f"[PRIVATE_DATA] {', '.join(private_data_injected)}")

        interrupt_data_injected = result.get("interrupt_data_injected", [])
        if interrupt_data_injected:
            lines.append(f"[INTERRUPT_DATA] {', '.join(interrupt_data_injected)}")

        response_text = extract_response_text(result)
        if response_text:
            lines.append(f"Response: {truncate_fn(response_text)}")

    if result is not None:
        if isinstance(result, dict):
            if "result" in result:
                result_to_display = result["result"]
                result_str = truncate_fn(str(result_to_display))
                lines.append(f"Result: {result_str}")
            elif "response" not in result and "responses" not in result:
                result_str = truncate_fn(str(result))
                lines.append(f"Result: {result_str}")
        else:
            result_str = truncate_fn(str(result))
            lines.append(f"Result: {result_str}")

    if not lines:
        return

    for idx, line in enumerate(lines):
        connector = "'--" if idx == len(lines) - 1 else "|--"
        print(f"  {connector} {line}")


# MARK: Result Display


def print_result_content(
    result: Any,
    file_writer: FileWriter,
) -> None:
    """Print result content, handling large results with file fallback."""
    try:
        result_json = result_to_json(result)
        print_or_write_result(
            result_json,
            "json",
            MAX_RESULT_LINES,
            None,
            file_writer,
        )
    except (TypeError, ValueError, RecursionError):
        result_str = str(result)
        print_or_write_result(
            result_str,
            "txt",
            None,
            MAX_INLINE_RESULT_LENGTH,
            file_writer,
        )


def print_or_write_result(
    content: str,
    file_ext: str,
    max_lines: int | None,
    max_chars: int | None,
    file_writer: FileWriter,
) -> None:
    """Print result or write to file if too large."""
    should_write = file_writer.should_write_to_file(
        content,
        max_lines=max_lines,
        max_chars=max_chars,
    )

    if should_write:
        file_path, file_size = file_writer.write_result(content, file_ext)
        stats = file_writer.get_content_stats(content)

        if max_lines:
            print(f"Result too large for terminal display ({stats['line_count']} lines)")
        else:
            print("Result too large for terminal display")

        print(f"Written to: {file_path}")
        print(f"File size: {format_file_size(file_size)}")
    else:
        print(content)


# MARK: Event Labels

EVENT_LABEL_MAP: dict[str, str] = {
    "info": "INFO",
    "success": "SUCCESS",
    "warning": "WARNING",
    "warn": "WARNING",
    "error": "ERROR",
    "tool": "TOOL",
    "system": "SYSTEM",
    "model": "MODEL",
    "agent": "AGENT",
    "mcp": "MCP",
    "status": "STATUS",
}


def get_event_label(event_type: str) -> str:
    """Get display label for an event type."""
    return EVENT_LABEL_MAP.get(event_type, str(event_type).upper())


# MARK: Tool Details


def build_tool_start_details_simple(
    tool_type: str | None,
    agent_name: str | None,
    tool_args: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build tool start details for simple reporter (without event ID)."""
    if not (tool_type or agent_name or tool_args):
        return None

    details: dict[str, Any] = {}
    if tool_type:
        details["Type"] = tool_type
    if agent_name:
        details["Agent"] = agent_name
    if tool_args:
        arg_keys = tool_args.get("arg_keys")
        if isinstance(arg_keys, list):
            safe_keys = [str(k) for k in arg_keys]
        else:
            safe_keys = [str(k) for k in tool_args.keys()]
        details["Args"] = truncate_result(str({"arg_keys": safe_keys}))
    return details


# MARK: Response Normalization


def normalize_response_text(extracted: str | None, response: str) -> str:
    """Normalize response text for display."""
    if isinstance(extracted, str):
        return extracted.strip()
    if isinstance(response, str):
        return response.strip()
    return str(response)
