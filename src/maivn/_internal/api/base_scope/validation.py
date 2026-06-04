"""Tool configuration validation for BaseScope."""

# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces.repositories import ToolRepoInterface

# MARK: Types


class _ToolScope(Protocol):
    name: str | None

    def list_tools(self) -> list[BaseTool]: ...


class _AgentScope(_ToolScope, Protocol):
    use_as_final_output: bool


class _SwarmScope(_ToolScope, Protocol):
    agents: Sequence[_AgentScope]


# MARK: Validation Helpers


def collect_all_tools(scope: object) -> list[BaseTool]:
    """Collect tools from scope, member agents, and parent swarm."""
    all_tools = list(_tool_repo_for(scope).list_tools())
    all_tools.extend(_collect_agent_tools(scope))
    all_tools.extend(_collect_swarm_tools(scope))
    return all_tools


def _collect_agent_tools(scope: object) -> list[BaseTool]:
    if not hasattr(scope, "agents"):
        return []
    tools: list[BaseTool] = []
    for agent in cast(_SwarmScope, scope).agents:
        tools.extend(agent.list_tools())
    return tools


def _collect_swarm_tools(scope: object) -> list[BaseTool]:
    swarm = cast(object | None, getattr(scope, "_swarm", None))
    if swarm is None:
        return []
    return list(cast(_ToolScope, swarm).list_tools())


def get_scope_name_for_validation(scope: object) -> str:
    """Build a human-readable scope name for validation error messages."""
    tool_scope = cast(_ToolScope, scope)
    name = tool_scope.name or scope.__class__.__name__
    if hasattr(scope, "agents"):
        return f"Swarm '{name}' (including all agents)"
    swarm = cast(object | None, getattr(scope, "_swarm", None))
    if swarm is not None:
        parent = cast(_ToolScope, swarm)
        return f"Agent '{name}' (including parent Swarm '{parent.name}')"
    return f"{scope.__class__.__name__} '{name}'"


def _tool_repo_for(scope: object) -> ToolRepoInterface:
    return cast(ToolRepoInterface, getattr(scope, "_tool_repo"))  # noqa: B009


# MARK: Flag Validation


def validate_tool_flags_per_scope(scope: object) -> list[str]:
    """Validate final_tool counts per independent execution scope.

    Each execution scope (agent's own tools, swarm's own tools) may have at most
    one final_tool. Multiple agents within a swarm may each have their own
    final_tool; when they do, exactly one agent must be marked
    use_as_final_output to disambiguate the swarm's final response.

    `always_execute` and `final_tool` are not mutually exclusive — they describe
    frequency and role respectively, and the orchestrator composes them.
    """
    errors: list[str] = []

    if hasattr(scope, "agents"):
        errors.extend(_validate_swarm_scope(scope))
    else:
        errors.extend(_validate_agent_scope(scope))

    return errors


def _validate_swarm_scope(swarm: object) -> list[str]:
    errors: list[str] = []
    swarm_scope = cast(_SwarmScope, swarm)
    swarm_name = swarm_scope.name or swarm.__class__.__name__

    swarm_tools = list(_tool_repo_for(swarm).list_tools())
    errors.extend(
        _validate_single_final_tool_in_list(
            swarm_tools,
            scope_label=f"Swarm '{swarm_name}' (swarm-scope tools)",
        )
    )

    agents_with_final_tool: list[_AgentScope] = []
    for agent in swarm_scope.agents or []:
        agent_tools = list(agent.list_tools())
        agent_name = agent.name or "unknown"
        errors.extend(
            _validate_single_final_tool_in_list(
                agent_tools,
                scope_label=f"Agent '{agent_name}' within Swarm '{swarm_name}'",
            )
        )
        if any(getattr(t, "final_tool", False) for t in agent_tools):
            agents_with_final_tool.append(agent)

    errors.extend(
        _validate_designated_final_agent_when_ambiguous(
            swarm=swarm,
            swarm_tools=swarm_tools,
            agents_with_final_tool=agents_with_final_tool,
        )
    )

    return errors


def _validate_agent_scope(agent: object) -> list[str]:
    errors: list[str] = []
    agent_scope = cast(_AgentScope, agent)
    agent_name = agent_scope.name or agent.__class__.__name__

    agent_tools = list(_tool_repo_for(agent).list_tools())
    errors.extend(
        _validate_single_final_tool_in_list(
            agent_tools,
            scope_label=f"Agent '{agent_name}'",
        )
    )

    parent_swarm = cast(object | None, getattr(agent, "_swarm", None))
    if parent_swarm is not None:
        parent_scope = cast(_ToolScope, parent_swarm)
        swarm_name = parent_scope.name or type(parent_swarm).__name__
        swarm_tools = list(parent_scope.list_tools())
        errors.extend(
            _validate_single_final_tool_in_list(
                swarm_tools,
                scope_label=f"Parent Swarm '{swarm_name}' (swarm-scope tools)",
            )
        )

    return errors


def _validate_single_final_tool_in_list(
    tools: Sequence[BaseTool],
    *,
    scope_label: str,
) -> list[str]:
    final_tools = [t for t in tools if getattr(t, "final_tool", False)]
    if len(final_tools) <= 1:
        return []
    final_names = ", ".join(f"'{t.name}'" for t in final_tools)
    return [
        f"\n[ERROR] Multiple tools marked with final_tool=True: {final_names}\n"
        + f"  SCOPE: {scope_label}\n"
        + "  ISSUE: Only ONE tool per scope can be designated as the final output tool.\n"
        + "  FIX: Remove 'final_tool=True' from all but one tool in this scope.\n"
    ]


def _validate_designated_final_agent_when_ambiguous(
    *,
    swarm: object,
    swarm_tools: Sequence[BaseTool],
    agents_with_final_tool: Sequence[_AgentScope],
) -> list[str]:
    """Require exactly one use_as_final_output agent when final-tool ownership is ambiguous.

    Ambiguity cases:
    - Two or more agents each have a final_tool.
    - An agent has a final_tool AND the swarm itself also has a final_tool.
    """
    swarm_has_final_tool = any(getattr(t, "final_tool", False) for t in swarm_tools)
    ambiguous = len(agents_with_final_tool) >= 2 or (
        swarm_has_final_tool and len(agents_with_final_tool) >= 1
    )
    if not ambiguous:
        return []

    designated = [
        agent for agent in cast(_SwarmScope, swarm).agents or [] if agent.use_as_final_output
    ]
    if len(designated) == 1:
        return []

    swarm_scope = cast(_SwarmScope, swarm)
    swarm_name = swarm_scope.name or swarm.__class__.__name__
    agent_names = ", ".join(f"'{agent.name or 'unknown'}'" for agent in agents_with_final_tool)
    extras: list[str] = []
    if swarm_has_final_tool:
        extras.append("swarm-scope")
    if agents_with_final_tool:
        extras.append(f"agents: {agent_names}")
    ownership = "; ".join(extras)

    if not designated:
        return [
            f"\n[ERROR] Ambiguous final_tool ownership in Swarm '{swarm_name}'\n"
            + f"  Final tools declared on: {ownership}\n"
            + "  ISSUE: When multiple scopes in a swarm declare final_tool, the swarm's\n"
            + "         final response agent must be designated explicitly.\n"
            + "  FIX: Set use_as_final_output=True on exactly one agent.\n"
        ]

    designated_names = ", ".join(f"'{agent.name or 'unknown'}'" for agent in designated)
    return [
        "\n[ERROR] Multiple swarm agents marked use_as_final_output=True\n"
        + f"  SCOPE: Swarm '{swarm_name}'\n"
        + f"  Agents: {designated_names}\n"
        + "  FIX: Set use_as_final_output=True on exactly one agent.\n"
    ]


def validate_swarm_final_output_agents(scope: object) -> list[str]:
    """Validate that at most one swarm agent has use_as_final_output=True."""
    if not hasattr(scope, "agents"):
        return []
    swarm_scope = cast(_SwarmScope, scope)
    agents = list(swarm_scope.agents or [])
    flagged = [agent for agent in agents if agent.use_as_final_output]
    if len(flagged) <= 1:
        return []
    agent_names = ", ".join(f"'{agent.name or 'unknown'}'" for agent in flagged)
    return [
        "\n[ERROR] Multiple swarm agents marked use_as_final_output=True\n"
        + f"  SCOPE: Swarm '{swarm_scope.name}'\n"
        + f"  Agents: {agent_names}\n"
        + "  FIX: Set use_as_final_output=True on exactly one agent.\n"
    ]


def raise_validation_error(errors: list[str]) -> None:
    """Raise a ValueError with formatted tool configuration errors."""
    error_msg = (
        "\n"
        + "=" * 80
        + "\n"
        + "TOOL CONFIGURATION ERROR\n"
        + "=" * 80
        + "".join(errors)
        + "=" * 80
        + "\n"
    )
    raise ValueError(error_msg)


__all__ = [
    "collect_all_tools",
    "get_scope_name_for_validation",
    "raise_validation_error",
    "validate_swarm_final_output_agents",
    "validate_tool_flags_per_scope",
]
