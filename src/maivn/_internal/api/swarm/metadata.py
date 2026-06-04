"""Swarm metadata enrichment and agent roster building."""

# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, TypeAlias, cast

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

from maivn._internal.core.entities.tools import BaseTool

# MARK: Types

ConfigOverride: TypeAlias = dict[str, object]


class AgentLike(Protocol):
    @property
    def id(self) -> str: ...

    name: str | None
    description: str | None
    use_as_final_output: bool
    included_nested_synthesis: bool | Literal["auto"]

    def list_tools(self) -> list[BaseTool]: ...

    def resolve_memory_config(self, override: object = None) -> MemoryConfig | None: ...

    def build_memory_asset_payloads(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...


class SwarmLike(Protocol):
    @property
    def id(self) -> str: ...

    name: str | None
    description: str | None
    system_prompt: str | SystemMessage | None
    agents: Sequence[AgentLike]

    def list_tools(self) -> list[BaseTool]: ...

    def resolve_memory_config(self, override: object = None) -> MemoryConfig | None: ...

    def build_memory_asset_payloads(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...


# MARK: State Metadata Enrichment


def enrich_state_metadata(
    swarm: SwarmLike,
    state: SessionRequest,
    *,
    memory_config: MemoryConfig | ConfigOverride | None = None,
) -> None:
    """Enrich state with typed swarm and memory asset configuration."""
    invocation_tool_map = build_invocation_tool_map(swarm)
    agent_id_to_name = {agent.id: agent.name for agent in swarm.agents if agent.id and agent.name}
    roster: list[SwarmAgentConfig] = [
        build_agent_roster_entry(
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


def _swarm_has_final_tool(swarm: SwarmLike) -> bool:
    """True when any swarm-scope tool is marked ``final_tool=True``."""
    return any(tool.final_tool for tool in swarm.list_tools())


def _resolve_system_prompt(swarm: SwarmLike) -> str | None:
    """Return the swarm system prompt text if present."""
    system_prompt = swarm.system_prompt
    if isinstance(system_prompt, SystemMessage):
        content = cast(object, system_prompt.content)
        return content if isinstance(content, str) else None
    if isinstance(system_prompt, str) and system_prompt.strip():
        return system_prompt
    return None


# MARK: Config Helpers


def _apply_swarm_memory_assets_config(swarm: SwarmLike, state: SessionRequest) -> None:
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


def build_invocation_tool_map(swarm: SwarmLike) -> dict[str, str]:
    """Build mapping of agent IDs to invocation tool IDs."""
    tool_map: dict[str, str] = {}
    for agent in swarm.agents:
        agent_id = agent.id
        if agent_id:
            tool_map[agent_id] = create_uuid(f"agent_invoke_{agent_id}")
    return tool_map


def build_agent_roster_entry(
    swarm: SwarmLike,
    agent: AgentLike,
    invocation_tool_map: dict[str, str],
    *,
    agent_id_to_name: dict[str, str],
) -> SwarmAgentConfig:
    """Build a roster entry for an agent."""
    agent_id = agent.id
    agent_name = agent.name
    agent_description = agent.description
    tools = agent.list_tools()
    tool_count = len(tools)

    has_final_tool = any(tool.final_tool for tool in tools)
    included_nested_synthesis = _normalize_included_nested_synthesis(
        agent.included_nested_synthesis
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

    roster_entry: dict[str, object] = {
        "agent_id": agent_id,
        "name": agent_name,
        "description": agent_description,
        "use_as_final_output": agent.use_as_final_output,
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
    tools: list[BaseTool],
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
        deps: list[object] = list(tool.dependencies)
        if not deps:
            deps = list(cast(list[object], getattr(tool, "_dependencies", []) or []))
        if not deps:
            deps = list(cast(list[object], getattr(tool, "__maivn_pending_deps__", []) or []))
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
    roster_entry: dict[str, object],
    agent: AgentLike,
) -> None:
    """Expose member agent memory defaults to server-side swarm policy checks."""
    resolved = agent.resolve_memory_config(None)
    if isinstance(resolved, MemoryConfig) and resolved.is_configured():
        roster_entry["memory_config"] = resolved.model_dump(exclude_none=True)


def _apply_agent_memory_assets(
    roster_entry: dict[str, object],
    agent: AgentLike,
    swarm: SwarmLike,
) -> None:
    """Expose agent-defined skills/resources in swarm roster metadata."""
    raw_skill_payloads, raw_resource_payloads = agent.build_memory_asset_payloads(
        default_agent_id=agent.id,
        default_swarm_id=swarm.id,
    )
    skill_payloads = list(raw_skill_payloads)
    resource_payloads = list(raw_resource_payloads)

    if skill_payloads:
        roster_entry["memory_defined_skills"] = skill_payloads
    if resource_payloads:
        roster_entry["memory_bound_resources"] = resource_payloads


def _normalize_included_nested_synthesis(value: object) -> bool | Literal["auto"]:
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
    agent: AgentLike,
    included_nested_synthesis: bool | Literal["auto"],
    has_final_tool: bool,
    tool_count: int,
) -> str:
    if included_nested_synthesis is True:
        return "Always request this agent's synthesized response for downstream context."
    if included_nested_synthesis is False:
        return "Prefer raw tool results; request synthesis only when explicitly required."

    description = str(agent.description or "").lower()
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


__all__ = [
    "build_agent_roster_entry",
    "build_invocation_tool_map",
    "enrich_state_metadata",
]
