"""Shared reporter utilities and base components.

This module provides common formatting logic and utilities shared between
the rich and simple terminal reporter implementations.
"""

from __future__ import annotations

from typing import Any

from .._formatters import format_elapsed_time, format_event_id, truncate_result

# MARK: Tool Type Configuration


TOOL_TYPE_PREFIXES: dict[str, str] = {
    "agent": "AGENT",
    "model": "MODEL",
    "system": "SYSTEM",
    "mcp": "MCP",
}
"""Mapping from tool type to display prefix."""

DEFAULT_TOOL_PREFIX: str = "FUNC"
"""Default prefix when tool type is unknown."""


def get_tool_prefix(tool_type: str | None) -> str:
    """Get display prefix for a tool type.

    Args:
        tool_type: Type of tool (agent, model, system, mcp, func)

    Returns:
        Display prefix string
    """
    normalized = (tool_type or "").lower()
    return TOOL_TYPE_PREFIXES.get(normalized, DEFAULT_TOOL_PREFIX)


# MARK: Tool Detection


def is_reevaluate_system_tool(tool_name: str | None, tool_type: str | None) -> bool:
    """Check if this is a reevaluate system tool.

    Args:
        tool_name: Name of the tool
        tool_type: Type of the tool

    Returns:
        True if this is a reevaluate system tool
    """
    return str(tool_name or "").lower() == "reevaluate" and str(tool_type or "").lower() == "system"


# MARK: Tool Arguments


def extract_safe_arg_keys(tool_args: dict[str, Any] | None) -> list[str]:
    """Extract safe argument keys from tool args.

    Tool args may include server-injected private_data values.
    This extracts only the keys for safe display.

    Args:
        tool_args: Tool arguments dictionary

    Returns:
        List of argument keys as strings
    """
    if not tool_args:
        return []

    arg_keys = tool_args.get("arg_keys")
    if isinstance(arg_keys, list):
        return [str(k) for k in arg_keys]
    return [str(k) for k in tool_args.keys()]


def format_tool_args_display(tool_args: dict[str, Any] | None) -> str | None:
    """Format tool arguments for display.

    Args:
        tool_args: Tool arguments dictionary

    Returns:
        Truncated string representation of arg keys, or None if no args
    """
    if not tool_args:
        return None

    safe_keys = extract_safe_arg_keys(tool_args)
    return truncate_result(str({"arg_keys": safe_keys}))


# MARK: Detail Builders


def build_tool_start_details(
    event_id: str,
    tool_type: str | None,
    agent_name: str | None,
    tool_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build details dictionary for tool start event.

    Args:
        event_id: Event identifier
        tool_type: Type of tool
        agent_name: Name of executing agent
        tool_args: Tool arguments

    Returns:
        Details dictionary with formatted values
    """
    details: dict[str, Any] = {"Event ID": format_event_id(event_id)}

    if tool_type:
        details["Type"] = tool_type

    if agent_name:
        details["Agent"] = agent_name

    args_display = format_tool_args_display(tool_args)
    if args_display:
        details["Args"] = args_display

    return details


def build_error_details(
    error: str,
    event_id: str | None,
    elapsed_ms: int | None,
) -> dict[str, Any]:
    """Build details dictionary for error event.

    Args:
        error: Error message
        event_id: Optional event identifier
        elapsed_ms: Optional elapsed time in milliseconds

    Returns:
        Details dictionary with formatted values
    """
    details: dict[str, Any] = {"Error": error}

    if event_id:
        details["Event ID"] = format_event_id(event_id)

    if isinstance(elapsed_ms, int) and elapsed_ms > 0:
        details["Elapsed"] = format_elapsed_time(elapsed_ms)

    return details


# MARK: Result Processing


def collect_injected_data_info(result: Any) -> list[tuple[str, str, list[str]]]:
    """Collect injected data information from a result.

    Args:
        result: Tool execution result

    Returns:
        List of tuples: (key, label, injected_keys)
    """
    if not isinstance(result, dict):
        return []

    injected_config = [
        ("private_data_injected", "Private Data"),
        ("interrupt_data_injected", "Interrupt Data"),
    ]

    items: list[tuple[str, str, list[str]]] = []
    for key, label in injected_config:
        injected_keys = result.get(key, [])
        if injected_keys:
            items.append((key, label, injected_keys))

    return items


def extract_response_text(result: Any) -> str | None:
    """Extract response text from a result.

    Args:
        result: Tool execution result

    Returns:
        Response text if present and non-empty, otherwise None
    """
    if not isinstance(result, dict):
        return None

    responses = result.get("responses")
    if isinstance(responses, list):
        for item in reversed(responses):
            if isinstance(item, str):
                response_text = item.strip()
                if response_text:
                    return response_text

    response_value = result.get("response")
    if not isinstance(response_value, str):
        return None

    response_text = response_value.strip()
    return response_text if response_text else None


def extract_result_for_display(
    result: Any,
    tool_info: dict[str, Any],
) -> Any | None:
    """Extract the displayable result portion.

    Filters out arguments that were passed as input (to avoid duplication),
    and handles the 'result' wrapper if present.

    Args:
        result: Tool execution result
        tool_info: Tool execution information

    Returns:
        Result to display, or None if nothing to show
    """
    if result is None:
        return None

    tool_name = str(tool_info.get("name") or "").lower()
    if tool_name == "reevaluate":
        return None

    result_to_display = result
    if isinstance(result, dict) and "result" in result:
        result_to_display = result["result"]

    tool_args = tool_info.get("tool_args")
    if isinstance(result_to_display, dict) and isinstance(tool_args, dict):
        # Filter out keys that match input args
        result_to_display = {
            k: v
            for k, v in result_to_display.items()
            if k not in tool_args or tool_args.get(k) != v
        }
        if not result_to_display:
            return None

    return result_to_display


__all__ = [
    "DEFAULT_TOOL_PREFIX",
    "TOOL_TYPE_PREFIXES",
    "build_error_details",
    "build_tool_start_details",
    "collect_injected_data_info",
    "extract_response_text",
    "extract_result_for_display",
    "extract_safe_arg_keys",
    "format_tool_args_display",
    "get_tool_prefix",
    "is_reevaluate_system_tool",
]
