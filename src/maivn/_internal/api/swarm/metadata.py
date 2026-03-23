"""Swarm metadata enrichment and agent roster building."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maivn_shared import (
    SWARM_INVOCATION_INTENT_METADATA_KEY,
    MemoryConfig,
    SessionRequest,
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
    """Enrich state metadata with swarm information."""
    metadata = dict(state.metadata) if state.metadata else {}

    metadata[SWARM_INVOCATION_INTENT_METADATA_KEY] = True
    metadata["swarm_id"] = swarm.id
    metadata["swarm_name"] = swarm.name or swarm.__class__.__name__

    if swarm.description:
        metadata["swarm_description"] = swarm.description

    _add_system_prompt_metadata(swarm, metadata)

    resolved_swarm_memory_config = swarm.resolve_memory_config(memory_config)
    state.memory_config = MemoryConfig.merge(
        state.memory_config if isinstance(state.memory_config, MemoryConfig) else None,
        resolved_swarm_memory_config,
    )
    swarm.apply_memory_assets_to_metadata(
        metadata,
        overwrite=True,
        default_swarm_id=swarm.id,
    )
    _add_agent_roster_metadata(swarm, metadata)

    state.metadata = metadata


def _add_system_prompt_metadata(swarm: Swarm, metadata: dict[str, Any]) -> None:
    """Add system prompt to metadata if present."""
    system_prompt = getattr(swarm, "system_prompt", None)
    if isinstance(system_prompt, SystemMessage):
        metadata["swarm_system_prompt"] = system_prompt.content
    elif isinstance(system_prompt, str) and system_prompt.strip():
        metadata["swarm_system_prompt"] = system_prompt


# MARK: Agent Roster


def _add_agent_roster_metadata(swarm: Swarm, metadata: dict[str, Any]) -> None:
    """Add agent roster and invocation tool map to metadata."""
    invocation_tool_map = _build_invocation_tool_map(swarm)
    roster = [
        _build_agent_roster_entry(swarm, agent, invocation_tool_map) for agent in swarm.agents
    ]
    metadata["swarm_agent_roster"] = roster
    metadata["swarm_agent_invocation_tool_map"] = invocation_tool_map


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
) -> dict[str, Any]:
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
    _apply_agent_memory_config(roster_entry, agent)
    _apply_agent_memory_assets(roster_entry, agent, swarm)
    return roster_entry


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


def _normalize_included_nested_synthesis(value: Any) -> bool | str:
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
    included_nested_synthesis: bool | str,
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
