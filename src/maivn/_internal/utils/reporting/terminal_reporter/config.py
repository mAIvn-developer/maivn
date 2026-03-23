"""Configuration constants for terminal reporters.
Centralizes display limits, file naming, and style constants.
Shared by rich and simple reporter implementations.
"""

from __future__ import annotations

from typing import Final

from maivn._internal.utils.env_parsing import read_bool_env, read_int_env, read_str_env

# MARK: Display Limits

# MARK: - Result Display
MAX_RESULT_LINES: Final[int] = 40
"""Maximum lines to display inline before writing to file."""

MAX_INLINE_RESULT_LENGTH: Final[int] = 25000
"""Maximum character length for inline text results."""

# MARK: - Truncation
RESULT_TRUNCATION_LENGTH: Final[int] = 200
"""Length at which to truncate tool results in display."""

RESULT_TRUNCATION_SUFFIX: Final[str] = "..."
"""Suffix to append to truncated results."""

# MARK: File Output

# MARK: - Paths
LOGS_DIRECTORY: Final[str] = "logs"
"""Directory for output files."""

# MARK: - Naming
RESULT_FILENAME_PREFIX: Final[str] = "final_result"
"""Prefix for result output files."""

TIMESTAMP_FORMAT: Final[str] = "%Y%m%d_%H%M%S"
"""Timestamp format for output filenames."""

# MARK: Event Types

# MARK: - Colors
EVENT_COLORS: Final[dict[str, str]] = {
    "info": "blue",
    "success": "green",
    "warning": "yellow",
    "error": "red",
    "assignment": "magenta",
    "tool": "cyan",
    "model": "magenta",
    "mcp": "green",
    "status": "cyan",
}
"""Color mapping for different event types."""

DEFAULT_EVENT_COLOR: Final[str] = "white"
"""Default color for unknown event types."""

# MARK: - Icons
EVENT_ICONS: Final[dict[str, str]] = {
    "info": "[i]",
    "success": "[OK]",
    "warning": "[!]",
    "error": "[X]",
    "assignment": "=>",
    "tool": "[RUN]",
    "model": "[MODEL]",
    "mcp": "[MCP]",
    "status": "[STATUS]",
}
"""Icon mapping for different event types (ASCII only for Windows compatibility)."""

DEFAULT_EVENT_ICON: Final[str] = "*"
"""Default icon for unknown event types (ASCII only for Windows compatibility)."""

# MARK: Progress Display

PROGRESS_REFRESH_RATE: Final[int] = 10
"""Refresh rate for live progress display (Hz)."""

DEFAULT_PROGRESS_DESCRIPTION: Final[str] = "Processing..."
"""Default description for progress indicators."""

# MARK: Styling

# MARK: - Border Styles
HEADER_BORDER_STYLE: Final[str] = "cyan"
"""Border style for headers."""

SECTION_BORDER_STYLE: Final[str] = "cyan"
"""Border style for sections."""

SUMMARY_BORDER_STYLE: Final[str] = "green"
"""Border style for summary tables."""

RESULT_BORDER_STYLE: Final[str] = "magenta"
"""Border style for result panels."""

ERROR_BORDER_STYLE: Final[str] = "red"
"""Border style for error panels."""

# MARK: - Text Styles
MODEL_TOOL_PREFIX_STYLE: Final[str] = "dim cyan"
"""Style for [MODEL] prefix."""

MODEL_TOOL_NAME_STYLE: Final[str] = "dim magenta"
"""Style for MODEL tool names."""

RESULT_LABEL_STYLE: Final[str] = "dim"
"""Style for result labels."""

RESULT_VALUE_STYLE: Final[str] = "cyan dim"
"""Style for result values."""

# MARK: Layout

# MARK: - Padding
HEADER_PADDING: Final[tuple[int, int]] = (1, 2)
"""Padding for header panels (vertical, horizontal)."""

SECTION_PADDING: Final[tuple[int, int]] = (0, 2)
"""Padding for section panels (vertical, horizontal)."""

RESULT_PADDING: Final[tuple[int, int]] = (1, 2)
"""Padding for result panels (vertical, horizontal)."""

# MARK: Simple Reporter

# MARK: - Borders
SIMPLE_BORDER_CHAR: Final[str] = "="
"""Border character for simple reporter."""

SIMPLE_BORDER_LENGTH: Final[int] = 60
"""Border length for simple reporter."""

# MARK: - Box Characters
SIMPLE_BOX_HORIZONTAL: Final[str] = "-"
"""Horizontal box character for simple reporter."""

SIMPLE_BOX_VERTICAL: Final[str] = "|"
"""Vertical box character for simple reporter."""

SIMPLE_BOX_CORNERS: Final[tuple[str, str, str, str]] = ("+", "+", "+", "+")
"""Box corner characters (top-left, top-right, bottom-left, bottom-right)."""

# MARK: Debug (Temporary)


TERMINAL_SYSTEM_TOOL_STREAM_MODE: Final[str] = read_str_env(
    "MAIVN_TERMINAL_STREAM_MODE",
    default="focus",
)


TERMINAL_SYSTEM_TOOL_STREAM_MAX_FPS: Final[int] = read_int_env(
    "MAIVN_TERMINAL_STREAM_MAX_FPS",
    default=10,
)


# TEMP DEBUG: Keep the final in-place system-tool streaming panel visible when the tool completes.
# Default OFF. Enable by setting:
#   MAIVN_DEBUG_KEEP_SYSTEM_TOOL_STREAM_PANEL=1
DEBUG_KEEP_SYSTEM_TOOL_STREAM_PANEL: Final[bool] = read_bool_env(
    "MAIVN_DEBUG_KEEP_SYSTEM_TOOL_STREAM_PANEL",
    default=False,
)

# TEMP DEBUG: Show explicit per-chunk delimiter markers in system-tool streaming panels.
# Default OFF. Enable by setting:
#   MAIVN_DEBUG_SYSTEM_TOOL_STREAM_DELIMITERS=1
DEBUG_SYSTEM_TOOL_STREAM_DELIMITERS_ENABLED: Final[bool] = read_bool_env(
    "MAIVN_DEBUG_SYSTEM_TOOL_STREAM_DELIMITERS",
    default=False,
)

# TEMP DEBUG: Delimiter marker (ASCII only).
DEBUG_SYSTEM_TOOL_STREAM_DELIMITER: Final[str] = "|"

# TEMP DEBUG: Cap buffered debug streaming text to avoid runaway growth.
DEBUG_SYSTEM_TOOL_STREAM_BUFFER_MAX_CHARS: Final[int] = 20000
