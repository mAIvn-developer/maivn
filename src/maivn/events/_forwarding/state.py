"""State shared across normalized event forwarding targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# MARK: Tool Context


@dataclass
class ToolContext:
    name: str
    tool_type: str
    agent_name: str | None = None
    swarm_name: str | None = None


# MARK: Forwarding State


@dataclass
class NormalizedEventForwardingState:
    """Per-stream state for normalized event forwarding."""

    assistant_text_by_id: dict[str, str] = field(default_factory=dict)
    tool_context_by_id: dict[str, ToolContext] = field(default_factory=dict)
    system_tool_chunk_count_by_id: dict[str, int] = field(default_factory=dict)
    enrichment_support_by_reporter_type: dict[type[Any], tuple[bool, bool, bool]] = field(
        default_factory=dict
    )


# MARK: State Helpers


def remember_tool_context(
    state: NormalizedEventForwardingState,
    *,
    tool_id: str,
    tool_name: str,
    tool_type: str,
    agent_name: str | None,
    swarm_name: str | None,
) -> None:
    state.tool_context_by_id[tool_id] = ToolContext(
        name=tool_name,
        tool_type=tool_type,
        agent_name=agent_name,
        swarm_name=swarm_name,
    )


def clear_tool_state(state: NormalizedEventForwardingState, tool_id: str) -> None:
    state.tool_context_by_id.pop(tool_id, None)
    state.system_tool_chunk_count_by_id.pop(tool_id, None)


__all__ = [
    "NormalizedEventForwardingState",
    "ToolContext",
    "clear_tool_state",
    "remember_tool_context",
]
