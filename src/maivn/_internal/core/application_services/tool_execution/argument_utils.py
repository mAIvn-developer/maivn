"""Argument utilities for tool execution.

This module provides utilities for argument validation and pruning,
ensuring tools receive only the arguments they can accept.
"""

from __future__ import annotations

import inspect
from typing import Any

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import BaseTool, FunctionTool, McpTool, ModelTool

# MARK: Argument Pruning


def get_allowed_parameters(tool: BaseTool) -> set[str] | None:
    """Get the set of allowed parameter names for a tool.

    Args:
        tool: Tool to get parameters for

    Returns:
        Set of allowed parameter names, or None if tool accepts any args
    """
    if isinstance(tool, FunctionTool):
        return _get_function_parameters(tool)
    if isinstance(tool, ModelTool):
        return _get_model_fields(tool)
    if isinstance(tool, McpTool):
        return None  # MCP tools accept any args
    return None


def _get_function_parameters(tool: FunctionTool) -> set[str] | None:
    """Get allowed parameters from a function tool.

    Args:
        tool: Function tool to inspect

    Returns:
        Set of parameter names, or None if function accepts **kwargs
    """
    func = getattr(tool, "func", None)
    if not callable(func):
        return None

    sig = inspect.signature(func)

    # If function accepts **kwargs, allow any arguments
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return None

    return {
        name
        for name, param in sig.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }


def _get_model_fields(tool: ModelTool) -> set[str] | None:
    """Get allowed fields from a model tool.

    Args:
        tool: Model tool to inspect

    Returns:
        Set of field names, or None if no model defined
    """
    model_cls = getattr(tool, "model", None)
    if model_cls is None:
        return None
    return set(getattr(model_cls, "model_fields", {}).keys())


def prune_arguments(
    tool: BaseTool,
    args: dict[str, Any],
    logger: LoggerProtocol | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Remove arguments that the tool doesn't accept.

    Args:
        tool: Tool to prune arguments for
        args: Arguments to prune
        logger: Optional logger for debugging

    Returns:
        Tuple of (filtered_args, dropped_keys)
    """
    allowed = get_allowed_parameters(tool)

    if allowed is None:
        return args, []

    filtered = {k: v for k, v in args.items() if k in allowed}
    dropped = [k for k in args if k not in allowed]

    if dropped and logger:
        tool_name = getattr(tool, "name", "unknown")
        logger.debug("[ARG_UTILS] Dropped args for %s: %s", tool_name, dropped)

    return filtered, dropped


__all__ = [
    "get_allowed_parameters",
    "prune_arguments",
]
