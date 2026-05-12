"""Validation helpers for Swarm invocation."""

from __future__ import annotations

from typing import Any

# MARK: Final Tool Validation


def has_swarm_final_tools(swarm: Any) -> bool:
    """Check if swarm has any tools marked as final_tool.

    Note: ``final_tool=True`` on a tool is a *positioning* declaration — it
    says "if any tools are used, this one must be last." It does NOT force
    the tool to run. Forcing only happens when the caller invokes with
    ``force_final_tool=True``.
    """
    return any(bool(getattr(tool, "final_tool", False)) for tool in swarm.list_tools())


def validate_force_final_tool_request(swarm: Any, force_final_tool: bool) -> None:
    """Validate ``force_final_tool=True`` usage for swarm invocations.

    Only fires when the caller is forcing a final tool. Without
    ``force_final_tool``, a swarm-scope ``final_tool`` is only a positioning
    hint and does not conflict with a sub-agent's ``use_as_final_output``.

    Resolution paths when a final response must be forced:
      1. Swarm-scope ``final_tool`` — the swarm's own final tool produces output.
      2. Sub-agent ``use_as_final_output=True`` — that sub-agent's response is
         the swarm's response.
      3. Sub-agent owns a tool with ``final_tool=True`` — that agent's tool
         produces output (must be unambiguous: at most one such agent unless
         disambiguated by ``use_as_final_output``).

    Paths 1 and 2 are mutually exclusive *when force_final_tool is active*.
    """
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

    if swarm_final_tools and final_output_agents:
        agent_names = [getattr(agent, "name", None) or "?" for agent in final_output_agents]
        raise ValueError(
            "Swarm.invoke(force_final_tool=True) has conflicting final-output "
            "declarations: the swarm owns a swarm-scope `final_tool` and "
            f"sub-agent(s) {agent_names} are marked `use_as_final_output=True`. "
            "Choose one mechanism — they cannot be combined when forcing."
        )

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
