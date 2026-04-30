"""Invocation preparation helpers for Agent."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from maivn_shared import (
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SwarmConfig,
    SystemMessage,
    SystemToolsConfig,
)
from pydantic import BaseModel as PydanticBaseModel

from .invocation_state import InvocationState

# MARK: State Preparation


def prepare_invocation_state(
    agent: Any,
    messages: Sequence[BaseMessage],
    *,
    metadata: dict[str, Any] | None,
    memory_config: MemoryConfig | dict[str, Any] | None,
    system_tools_config: SystemToolsConfig | dict[str, Any] | None,
    orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None,
    memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None,
    swarm_config: SwarmConfig | dict[str, Any] | None,
    allow_private_in_system_tools: bool | None,
) -> InvocationState:
    prepared_messages = prepare_messages(agent, messages)

    agent.reject_reserved_memory_metadata_keys(metadata)
    merged_metadata = dict(metadata or {})
    resolved_memory_config = agent.resolve_memory_config(memory_config)
    resolved_system_tools_config = agent.resolve_system_tools_config(
        system_tools_config,
        allow_private_in_system_tools=allow_private_in_system_tools,
    )
    resolved_orchestration_config = agent.resolve_orchestration_config(orchestration_config)
    swarm = agent.get_swarm()
    resolved_memory_assets_config = resolve_memory_assets_config(
        agent,
        memory_assets_config,
        default_agent_id=agent.id,
        default_swarm_id=swarm.id if swarm is not None else None,
    )
    resolved_swarm_config = coerce_swarm_config(swarm_config)

    return InvocationState(
        prepared_messages=prepared_messages,
        merged_metadata=merged_metadata,
        resolved_memory_config=resolved_memory_config,
        resolved_system_tools_config=resolved_system_tools_config,
        resolved_orchestration_config=resolved_orchestration_config,
        resolved_memory_assets_config=resolved_memory_assets_config,
        resolved_swarm_config=resolved_swarm_config,
        swarm=swarm,
        agent_mode=getattr(agent, "hook_execution_mode", "tool"),
        swarm_mode=(getattr(swarm, "hook_execution_mode", "tool") if swarm is not None else "tool"),
    )


def prepare_messages(agent: Any, messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Prepare messages, injecting system message if needed."""
    messages_list = list(messages)
    has_system = any(isinstance(message, SystemMessage) for message in messages_list)
    if not has_system and agent._system_message is not None:
        messages_list = [agent._system_message, *messages_list]
    return messages_list


# MARK: Memory Assets


def build_memory_assets_config(
    agent: Any,
    *,
    default_agent_id: str | None = None,
    default_swarm_id: str | None = None,
) -> MemoryAssetsConfig | None:
    skills, resources = agent.build_memory_asset_payloads(
        default_agent_id=default_agent_id,
        default_swarm_id=default_swarm_id,
    )
    if not skills and not resources:
        return None
    return MemoryAssetsConfig.model_validate(
        {
            "defined_skills": skills,
            "bound_resources": resources,
        }
    )


def coerce_memory_assets_config(value: Any) -> MemoryAssetsConfig | None:
    if value is None:
        return None
    if isinstance(value, MemoryAssetsConfig):
        return value
    if isinstance(value, dict):
        return MemoryAssetsConfig.model_validate(value)
    raise TypeError("memory_assets_config must be a MemoryAssetsConfig, dictionary, or None")


def resolve_memory_assets_config(
    agent: Any,
    override: Any = None,
    *,
    default_agent_id: str | None = None,
    default_swarm_id: str | None = None,
) -> MemoryAssetsConfig | None:
    base = build_memory_assets_config(
        agent,
        default_agent_id=default_agent_id,
        default_swarm_id=default_swarm_id,
    )
    override_config = coerce_memory_assets_config(override)
    if override_config is None:
        return base
    if base is None:
        return override_config
    return MemoryAssetsConfig(
        defined_skills=override_config.defined_skills or base.defined_skills,
        bound_resources=override_config.bound_resources or base.bound_resources,
        recall_turn_active=(
            override_config.recall_turn_active
            if override_config.recall_turn_active is not None
            else base.recall_turn_active
        ),
    )


def coerce_swarm_config(value: Any) -> SwarmConfig | None:
    if value is None:
        return None
    if isinstance(value, SwarmConfig):
        return value
    if isinstance(value, dict):
        return SwarmConfig.model_validate(value)
    raise TypeError("swarm_config must be a SwarmConfig, dictionary, or None")


# MARK: Validation


def validate_invoke_params(
    agent: Any,
    force_final_tool: bool,
    targeted_tools: list[str] | None,
    structured_output: type[PydanticBaseModel] | None,
) -> None:
    """Validate invocation parameters for mutual exclusivity."""
    agent.validate_tool_configuration()

    if structured_output is not None and targeted_tools:
        raise ValueError("structured_output and targeted_tools are mutually exclusive.")
    if force_final_tool and targeted_tools:
        raise ValueError("force_final_tool and targeted_tools are mutually exclusive.")
    if force_final_tool and structured_output is None:
        validate_final_tool_exists(agent)


def validate_final_tool_exists(agent: Any) -> None:
    """Ensure at least one final_tool exists when force_final_tool is True."""
    all_tools = collect_all_tools(agent)
    final_tools = [tool for tool in all_tools if getattr(tool, "final_tool", False)]
    if not final_tools:
        raise ValueError(
            f"force_final_tool=True requires at least one tool with final_tool=True. "
            f"Agent '{agent.name}' has {len(all_tools)} tool(s) but none are final."
        )


def collect_all_tools(agent: Any) -> list[Any]:
    """Collect all tools from agent and parent swarm."""
    all_tools = list(agent.list_tools())
    swarm = agent.get_swarm()
    if swarm is not None:
        all_tools.extend(swarm.list_tools())
    return all_tools


__all__ = [
    "build_memory_assets_config",
    "coerce_memory_assets_config",
    "coerce_swarm_config",
    "collect_all_tools",
    "prepare_invocation_state",
    "prepare_messages",
    "resolve_memory_assets_config",
    "validate_final_tool_exists",
    "validate_invoke_params",
]
