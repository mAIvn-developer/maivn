"""Tool configuration validation for BaseScope."""

from __future__ import annotations

from typing import Any

from maivn._internal.core.entities.tools import BaseTool

# MARK: Validation Helpers


def collect_all_tools(scope: Any) -> list[BaseTool]:
    """Collect tools from scope, member agents, and parent swarm."""
    all_tools = list(scope._tool_repo.list_tools())
    all_tools.extend(_collect_agent_tools(scope))
    all_tools.extend(_collect_swarm_tools(scope))
    return all_tools


def _collect_agent_tools(scope: Any) -> list[BaseTool]:
    if not hasattr(scope, "agents"):
        return []
    tools: list[BaseTool] = []
    for agent in getattr(scope, "agents", []):
        tools.extend(agent.list_tools())
    return tools


def _collect_swarm_tools(scope: Any) -> list[BaseTool]:
    swarm = getattr(scope, "_swarm", None)
    if swarm is None:
        return []
    return list(swarm.list_tools())


def get_scope_name_for_validation(scope: Any) -> str:
    """Build a human-readable scope name for validation error messages."""
    name = scope.name or scope.__class__.__name__
    if hasattr(scope, "agents"):
        return f"Swarm '{name}' (including all agents)"
    swarm = getattr(scope, "_swarm", None)
    if swarm is not None:
        return f"Agent '{name}' (including parent Swarm '{swarm.name}')"
    return f"{scope.__class__.__name__} '{name}'"


# MARK: Flag Validation


def validate_tool_flags(
    always_execute_tools: list[BaseTool],
    final_tools: list[BaseTool],
    scope_name: str,
) -> list[str]:
    """Validate always_execute and final_tool flag combinations."""
    errors: list[str] = []
    errors.extend(_validate_single_final_tool(final_tools, scope_name))
    errors.extend(_validate_no_mixed_flags(always_execute_tools, final_tools, scope_name))
    return errors


def _validate_single_final_tool(
    final_tools: list[BaseTool],
    scope_name: str,
) -> list[str]:
    if len(final_tools) <= 1:
        return []
    final_names = ", ".join(f"'{t.name}'" for t in final_tools)
    return [
        f"\n[ERROR] Multiple tools marked with final_tool=True: {final_names}\n"
        f"  SCOPE: {scope_name}\n"
        f"  ISSUE: Only ONE tool can be designated as the final output tool.\n"
        f"  FIX: Remove 'final_tool=True' from all but one tool.\n"
    ]


def _validate_no_mixed_flags(
    always_execute_tools: list[BaseTool],
    final_tools: list[BaseTool],
    scope_name: str,
) -> list[str]:
    if not always_execute_tools or not final_tools:
        return []
    always_names = ", ".join(f"'{t.name}'" for t in always_execute_tools)
    final_names = ", ".join(f"'{t.name}'" for t in final_tools)
    return [
        f"\n[ERROR] Cannot mix always_execute=True and final_tool=True\n"
        f"  SCOPE: {scope_name}\n"
        f"  Tools with always_execute=True: {always_names}\n"
        f"  Tools with final_tool=True: {final_names}\n"
        f"  FIX: Choose ONE mode for this scope.\n"
    ]


def validate_swarm_final_output_agents(scope: Any) -> list[str]:
    """Validate that at most one swarm agent has use_as_final_output=True."""
    if not hasattr(scope, "agents"):
        return []
    agents = list(getattr(scope, "agents", []) or [])
    flagged = [a for a in agents if bool(getattr(a, "use_as_final_output", False))]
    if len(flagged) <= 1:
        return []
    agent_names = ", ".join(f"'{getattr(a, 'name', 'unknown')}'" for a in flagged)
    return [
        f"\n[ERROR] Multiple swarm agents marked use_as_final_output=True\n"
        f"  SCOPE: Swarm '{scope.name}'\n"
        f"  Agents: {agent_names}\n"
        f"  FIX: Set use_as_final_output=True on exactly one agent.\n"
    ]


def raise_validation_error(errors: list[str]) -> None:
    """Raise a ValueError with formatted tool configuration errors."""
    error_msg = (
        "\n" + "=" * 80 + "\n"
        "TOOL CONFIGURATION ERROR\n" + "=" * 80 + "".join(errors) + "=" * 80 + "\n"
    )
    raise ValueError(error_msg)


__all__ = [
    "collect_all_tools",
    "get_scope_name_for_validation",
    "raise_validation_error",
    "validate_swarm_final_output_agents",
    "validate_tool_flags",
]
