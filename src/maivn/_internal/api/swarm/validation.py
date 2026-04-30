"""Validation helpers for Swarm invocation."""

from __future__ import annotations

from typing import Any

# MARK: Final Tool Validation


def has_swarm_final_tools(swarm: Any) -> bool:
    """Check if swarm has any tools marked as final_tool."""
    return any(bool(getattr(tool, "final_tool", False)) for tool in swarm.list_tools())


def validate_force_final_tool_request(swarm: Any, force_final_tool: bool) -> None:
    """Validate force_final_tool usage for swarm invocations."""
    if not force_final_tool:
        return

    swarm_final_tools = [
        tool for tool in swarm.list_tools() if bool(getattr(tool, "final_tool", False))
    ]
    final_output_agents = [
        agent for agent in swarm.agents if bool(getattr(agent, "use_as_final_output", False))
    ]
    agents_with_final_tool = [
        agent
        for agent in swarm.agents
        if any(bool(getattr(tool, "final_tool", False)) for tool in agent.list_tools())
    ]

    if not swarm_final_tools and not final_output_agents and not agents_with_final_tool:
        raise ValueError(
            "Swarm.invoke(force_final_tool=True) requires at least one of: "
            "a swarm-scope tool with final_tool=True, an agent with use_as_final_output=True, "
            "or an agent that owns a tool with final_tool=True."
        )

    if agents_with_final_tool and not final_output_agents and len(agents_with_final_tool) > 1:
        raise ValueError(
            "Swarm.invoke(force_final_tool=True) is ambiguous: multiple agents own "
            "final_tool but none are marked use_as_final_output=True. "
            "Set use_as_final_output=True on the agent whose final_tool should be forced."
        )


__all__ = [
    "has_swarm_final_tools",
    "validate_force_final_tool_request",
]
