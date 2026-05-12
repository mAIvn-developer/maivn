"""Swarm metadata enrichment and agent roster building."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from maivn_shared import (
    AgentDependency,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionRequest,
    SwarmAgentConfig,
    SwarmConfig,
    SystemMessage,
    create_uuid,
)

if TYPE_CHECKING:
    from ..agent import Agent
    from .swarm import Swarm


# MARK: State Metadata Enrichment


def enrich_state_metadata(
    swarm: Swarm,
    state: SessionRequest,
    *,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
) -> None:
    """Enrich state with typed swarm and memory asset configuration."""
    invocation_tool_map = _build_invocation_tool_map(swarm)
    agent_id_to_name = {
        getattr(agent, "id", ""): getattr(agent, "name", "")
        for agent in swarm.agents
        if getattr(agent, "id", None) and getattr(agent, "name", None)
    }
    roster: list[SwarmAgentConfig] = [
        _build_agent_roster_entry(
            swarm, agent, invocation_tool_map, agent_id_to_name=agent_id_to_name
        )
        for agent in swarm.agents
    ]

    resolved_swarm_memory_config = swarm.resolve_memory_config(memory_config)
    state.memory_config = MemoryConfig.merge(
        state.memory_config if isinstance(state.memory_config, MemoryConfig) else None,
        resolved_swarm_memory_config,
    )
    _apply_swarm_memory_assets_config(swarm, state)
    state.swarm_config = SwarmConfig(
        invocation_intent=True,
        swarm_id=swarm.id,
        swarm_name=swarm.name or swarm.__class__.__name__,
        swarm_description=swarm.description,
        swarm_system_prompt=_resolve_system_prompt(swarm),
        agent_roster=roster,
        agent_invocation_tool_map=invocation_tool_map,
        swarm_has_final_tool=_swarm_has_final_tool(swarm),
    )


def _swarm_has_final_tool(swarm: Swarm) -> bool:
    """True when any swarm-scope tool is marked ``final_tool=True``."""
    return any(bool(getattr(tool, "final_tool", False)) for tool in swarm.list_tools())


def _resolve_system_prompt(swarm: Swarm) -> str | None:
    """Return the swarm system prompt text if present."""
    system_prompt = getattr(swarm, "system_prompt", None)
    if isinstance(system_prompt, SystemMessage):
        return system_prompt.content if isinstance(system_prompt.content, str) else None
    if isinstance(system_prompt, str) and system_prompt.strip():
        return system_prompt
    return None


# MARK: Config Helpers


def _apply_swarm_memory_assets_config(swarm: Swarm, state: SessionRequest) -> None:
    skills, resources = swarm.build_memory_asset_payloads(default_swarm_id=swarm.id)
    existing = (
        state.memory_assets_config
        if isinstance(state.memory_assets_config, MemoryAssetsConfig)
        else None
    )
    defined_skills = skills or (existing.defined_skills if existing is not None else [])
    bound_resources = resources or (existing.bound_resources if existing is not None else [])
    config = MemoryAssetsConfig.model_validate(
        {
            "defined_skills": defined_skills,
            "bound_resources": bound_resources,
        }
    )
    if config.is_configured():
        state.memory_assets_config = config


# MARK: Agent Roster


def _build_invocation_tool_map(swarm: Swarm) -> dict[str, str]:
    """Build mapping of agent IDs to invocation tool IDs."""
    tool_map: dict[str, str] = {}
    for agent in swarm.agents:
        agent_id = getattr(agent, "id", None)
        if agent_id:
            tool_map[agent_id] = create_uuid(f"agent_invoke_{agent_id}")
    return tool_map


def _build_agent_roster_entry(
    swarm: Swarm,
    agent: Agent,
    invocation_tool_map: dict[str, str],
    *,
    agent_id_to_name: dict[str, str],
) -> SwarmAgentConfig:
    """Build a roster entry for an agent."""
    agent_id = getattr(agent, "id", None)
    agent_name = getattr(agent, "name", None)
    agent_description = getattr(agent, "description", None)
    tools = agent.list_tools()
    tool_count = len(tools)

    has_final_tool = any(bool(getattr(tool, "final_tool", False)) for tool in tools)
    included_nested_synthesis = _normalize_included_nested_synthesis(
        getattr(agent, "included_nested_synthesis", "auto")
    )
    guidance = _build_included_nested_synthesis_guidance(
        agent=agent,
        included_nested_synthesis=included_nested_synthesis,
        has_final_tool=has_final_tool,
        tool_count=tool_count,
    )
    invokes_via_dependency = _collect_agent_dependency_targets(
        tools, agent_id_to_name=agent_id_to_name
    )

    roster_entry: dict[str, Any] = {
        "agent_id": agent_id,
        "name": agent_name,
        "description": agent_description,
        "use_as_final_output": bool(getattr(agent, "use_as_final_output", False)),
        "included_nested_synthesis": included_nested_synthesis,
        "included_nested_synthesis_guidance": guidance,
        "has_final_tool": has_final_tool,
        "invocation_tool_id": invocation_tool_map.get(agent_id or ""),
    }
    if invokes_via_dependency:
        roster_entry["invokes_via_dependency"] = invokes_via_dependency
    _apply_agent_memory_config(roster_entry, agent)
    _apply_agent_memory_assets(roster_entry, agent, swarm)
    return SwarmAgentConfig.model_validate(roster_entry)


def _collect_agent_dependency_targets(
    tools: list[Any],
    *,
    agent_id_to_name: dict[str, str],
) -> list[str]:
    """Walk tools for ``@depends_on_agent`` and return the swarm-resolvable
    target agent names (deduped, in declaration order).

    The orchestrator gets these so it knows which agents are already going to
    be invoked as a tool dependency by another roster member, so it can avoid
    scheduling a redundant separate stage. Only swarm-member targets are
    surfaced — out-of-roster agent_ids are dropped because the orchestrator
    can't act on them.

    Reads from ``BaseTool.dependencies`` (the canonical compiled location);
    falls back to the pre-compile ``_dependencies`` / ``__maivn_pending_deps__``
    attribute for tools whose decorator chain hasn't materialized yet.
    """
    targets: list[str] = []
    seen: set[str] = set()
    for tool in tools:
        deps: Any = getattr(tool, "dependencies", None)
        if not deps:
            deps = getattr(tool, "_dependencies", None)
        if not deps:
            deps = getattr(tool, "__maivn_pending_deps__", None)
        if not deps:
            continue
        for dep in deps:
            if not isinstance(dep, AgentDependency):
                continue
            target_id = getattr(dep, "agent_id", None)
            if not isinstance(target_id, str) or not target_id:
                continue
            target_name = agent_id_to_name.get(target_id)
            if not target_name or target_name in seen:
                continue
            targets.append(target_name)
            seen.add(target_name)
    return targets


# MARK: Roster Helpers


def _apply_agent_memory_config(
    roster_entry: dict[str, Any],
    agent: Agent,
) -> None:
    """Expose member agent memory defaults to server-side swarm policy checks."""
    resolver = getattr(agent, "resolve_memory_config", None)
    if not callable(resolver):
        return
    resolved = resolver(None)
    if isinstance(resolved, MemoryConfig) and resolved.is_configured():
        roster_entry["memory_config"] = resolved.model_dump(exclude_none=True)


def _apply_agent_memory_assets(
    roster_entry: dict[str, Any],
    agent: Agent,
    swarm: Swarm,
) -> None:
    """Expose agent-defined skills/resources in swarm roster metadata."""
    build_assets = getattr(agent, "build_memory_asset_payloads", None)
    if not callable(build_assets):
        return

    raw_assets = build_assets(
        default_agent_id=getattr(agent, "id", None),
        default_swarm_id=swarm.id,
    )
    if not (isinstance(raw_assets, tuple) and len(raw_assets) == 2):
        return

    raw_skill_payloads, raw_resource_payloads = raw_assets
    skill_payloads = (
        [item for item in raw_skill_payloads if isinstance(item, dict)]
        if isinstance(raw_skill_payloads, list)
        else []
    )
    resource_payloads = (
        [item for item in raw_resource_payloads if isinstance(item, dict)]
        if isinstance(raw_resource_payloads, list)
        else []
    )

    if skill_payloads:
        roster_entry["memory_defined_skills"] = skill_payloads
    if resource_payloads:
        roster_entry["memory_bound_resources"] = resource_payloads


def _normalize_included_nested_synthesis(value: Any) -> bool | Literal["auto"]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "auto":
            return "auto"
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return "auto"


def _build_included_nested_synthesis_guidance(
    *,
    agent: Agent,
    included_nested_synthesis: bool | Literal["auto"],
    has_final_tool: bool,
    tool_count: int,
) -> str:
    if included_nested_synthesis is True:
        return "Always request this agent's synthesized response for downstream context."
    if included_nested_synthesis is False:
        return "Prefer raw tool results; request synthesis only when explicitly required."

    description = str(getattr(agent, "description", "") or "").lower()
    aggregation_terms = (
        "synth",
        "summary",
        "summarize",
        "report",
        "plan",
        "director",
        "recommend",
        "strategy",
    )
    likely_aggregator = any(term in description for term in aggregation_terms)

    if likely_aggregator or tool_count >= 3:
        return (
            "Auto mode (default): keep as auto. This agent may aggregate multiple inputs, "
            "so set true only when very large payloads are expected and downstream steps "
            "need compressed narrative context."
        )
    if has_final_tool:
        return (
            "Auto mode (default): this agent has structured final output; keep auto and "
            "only override when narrative compression is required for large payloads."
        )
    return (
        "Auto mode (default): keep auto and request synthesis only when tool payloads "
        "become large enough to risk downstream context bloat."
    )


__all__ = ["enrich_state_metadata"]
