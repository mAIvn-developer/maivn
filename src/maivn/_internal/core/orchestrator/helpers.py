"""Helper functions and configuration for AgentOrchestrator."""

# pyright: strict
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, TypeAlias, cast

from maivn._internal.core.entities import BaseTool, FunctionTool, McpTool, MethodTool, ModelTool

# MARK: Constants

WINDOWS_PATH_RE: Final[re.Pattern[str]] = re.compile(r"[a-zA-Z]:\\")


# MARK: Types

RuntimeTool: TypeAlias = FunctionTool | MethodTool | ModelTool | McpTool

# MARK: Configuration


@dataclass(frozen=True)
class OrchestratorConfig:
    """Configuration parameters for orchestrator timeouts and execution."""

    http_timeout: float
    execution_timeout: float
    pending_event_timeout_s: float
    max_retries: int = 3
    enable_background_execution: bool = True


# MARK: Helper Functions


def extract_latest_response(responses: object) -> str | None:
    """Return the last non-empty string from a responses list, or None."""
    if not isinstance(responses, list):
        return None
    response_items = cast(list[object], responses)
    for item in reversed(response_items):
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def coerce_tool_list(
    tools: Sequence[BaseTool],
) -> Sequence[RuntimeTool]:
    """Coerce a generic tool list to the expected tool union type."""
    return cast(Sequence[RuntimeTool], tools)


def sanitize_user_facing_error_message(message: str) -> str:
    """Sanitize error messages to hide internal implementation details."""
    lowered = message.lower()
    if "llm payload contains private data values" in lowered:
        return message

    if lowered.startswith("agent execution failed"):
        parts = message.split(":", 1)
        if len(parts) == 2 and parts[1].strip():
            message = parts[1].strip()
            lowered = message.lower()

    suspicious_substrings = (
        "/",
        "\\\\",
        ".md",
        "maivn_",
        "importlib",
        "langgraph",
        "traceback",
        'file "',
    )

    if any(token in lowered for token in suspicious_substrings):
        return "An internal error occurred. Please try again."

    if WINDOWS_PATH_RE.search(message) is not None:
        return "An internal error occurred. Please try again."

    return message


__all__ = [
    "OrchestratorConfig",
    "coerce_tool_list",
    "extract_latest_response",
    "sanitize_user_facing_error_message",
]
