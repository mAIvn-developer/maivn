"""Formatting utilities for terminal output.

This module provides functions for formatting events, results, and display elements.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from typing import Any

from maivn_shared import dumps

from .config import (
    DEFAULT_EVENT_COLOR,
    DEFAULT_EVENT_ICON,
    EVENT_COLORS,
    EVENT_ICONS,
    RESULT_TRUNCATION_LENGTH,
    RESULT_TRUNCATION_SUFFIX,
)

# MARK: - Event Styling


def get_event_color(event_type: str) -> str:
    """Get color for an event type.

    Args:
        event_type: Type of event

    Returns:
        Color name for the event type
    """
    return EVENT_COLORS.get(event_type, DEFAULT_EVENT_COLOR)


def get_event_icon(event_type: str) -> str:
    """Get icon for an event type.

    Args:
        event_type: Type of event

    Returns:
        Icon character for the event type
    """
    return EVENT_ICONS.get(event_type, DEFAULT_EVENT_ICON)


# MARK: - ID Formatting


def format_session_id(session_id: str, length: int = 8) -> str:
    """Format session ID for display.

    Args:
        session_id: Full session ID
        length: Number of characters to show

    Returns:
        Formatted session ID
    """
    if len(session_id) <= length:
        return session_id
    return f"{session_id[:length]}..."


def format_event_id(event_id: str, length: int = 12) -> str:
    """Format event ID for display.

    Args:
        event_id: Full event ID
        length: Number of characters to show

    Returns:
        Formatted event ID
    """
    if len(event_id) <= length:
        return event_id
    return f"{event_id[:length]}..."


# MARK: - Time Formatting


def format_elapsed_time(elapsed_ms: int) -> str:
    """Format elapsed time for display.

    Args:
        elapsed_ms: Elapsed time in milliseconds

    Returns:
        Formatted time string
    """
    return f"{elapsed_ms}ms"


def format_total_time(elapsed_seconds: float) -> str:
    """Format total elapsed time for display.

    Args:
        elapsed_seconds: Elapsed time in seconds

    Returns:
        Formatted time string
    """
    return f"{elapsed_seconds:.2f}s"


# MARK: - Result Serialization


def result_to_json(result: Any, indent: int = 2) -> str:
    """Convert result to JSON string.

    Args:
        result: Result to serialize
        indent: JSON indentation level

    Returns:
        JSON string

    Raises:
        TypeError: If result cannot be serialized
    """
    return dumps(result, pretty=indent > 0)


def extract_text_from_response(response: Any) -> str | None:
    if isinstance(response, str):
        raw = response.strip()
        if raw.startswith(("[", "{")) and (
            "'type'" in raw or '"type"' in raw or "'text'" in raw or '"text"' in raw
        ):
            parsed: Any | None = None
            try:
                parsed = json.loads(raw)
            except Exception:
                try:
                    parsed = ast.literal_eval(raw)
                except Exception:
                    parsed = None

            if parsed is not None and not isinstance(parsed, str):
                extracted = extract_text_from_response(parsed)
                if extracted:
                    return extracted

        return response

    if isinstance(response, list):
        parts: list[str] = []
        for item in response:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                item_text = item.get("text")
                if isinstance(item_text, str) and item_text:
                    parts.append(item_text)
        return "".join(parts) if parts else None

    if isinstance(response, Mapping):
        direct_text = response.get("text")
        if isinstance(direct_text, str) and direct_text:
            return direct_text

        content = response.get("content")
        if content is not None:
            return extract_text_from_response(content)

    return None


# MARK: - Result Truncation


def truncate_result(
    result_str: str,
    max_length: int | None = None,
) -> str:
    """Truncate result string if too long.

    Args:
        result_str: Result string to truncate
        max_length: Maximum length (defaults to config value)

    Returns:
        Truncated string with suffix if needed
    """
    max_len = max_length or RESULT_TRUNCATION_LENGTH

    if len(result_str) <= max_len:
        return result_str

    truncate_at = max_len - len(RESULT_TRUNCATION_SUFFIX)
    return result_str[:truncate_at] + RESULT_TRUNCATION_SUFFIX


# MARK: - Size Formatting


def format_file_size(size_bytes: int) -> str:
    """Format file size for display.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted size string
    """
    if size_bytes < 1024:
        return f"{size_bytes} bytes"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


# MARK: - Content Analysis


def count_lines(content: str) -> int:
    """Count lines in content.

    Args:
        content: Content to count

    Returns:
        Number of lines
    """
    return content.count("\n") + 1


__all__ = [
    "count_lines",
    "extract_text_from_response",
    "format_elapsed_time",
    "format_event_id",
    "format_file_size",
    "format_session_id",
    "format_total_time",
    "get_event_color",
    "get_event_icon",
    "result_to_json",
    "truncate_result",
]
