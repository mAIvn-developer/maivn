"""Invocation preparation helpers for Agent."""

# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

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

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces.orchestrator_protocol import JsonObject

from .invocation_state import InvocationState

# MARK: Types


class _SwarmLike(Protocol):
    @property
    def id(self) -> str: ...

    hook_execution_mode: str

    def list_tools(self) -> list[BaseTool]: ...


class _AgentInvocationTarget(Protocol):
    @property
    def id(self) -> str: ...

    name: str | None
    hook_execution_mode: str

    def reject_reserved_memory_metadata_keys(self, metadata: object) -> None: ...

    def resolve_memory_config(self, override: object = None) -> MemoryConfig | None: ...

    def resolve_system_tools_config(
        self,
        override: object = None,
        *,
        allow_private_in_system_tools: bool | None = None,
    ) -> SystemToolsConfig | None: ...

    def resolve_orchestration_config(
        self,
        override: object = None,
    ) -> SessionOrchestrationConfig | None: ...

    def get_swarm(self) -> object | None: ...

    def build_memory_asset_payloads(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...

    def validate_tool_configuration(self) -> None: ...

    def list_tools(self) -> list[BaseTool]: ...


# MARK: State Preparation


def prepare_invocation_state(
    agent: _AgentInvocationTarget,
    messages: Sequence[BaseMessage],
    *,
    metadata: JsonObject | None,
    memory_config: MemoryConfig | dict[str, object] | None,
    system_tools_config: SystemToolsConfig | dict[str, object] | None,
    orchestration_config: SessionOrchestrationConfig | dict[str, object] | None,
    memory_assets_config: MemoryAssetsConfig | dict[str, object] | None,
    swarm_config: SwarmConfig | dict[str, object] | None,
    allow_private_in_system_tools: bool | None,
    system_message: SystemMessage | None,
) -> InvocationState:
    prepared_messages = prepare_messages(messages, system_message=system_message)

    agent.reject_reserved_memory_metadata_keys(metadata)
    merged_metadata = dict(metadata or {})
    resolved_memory_config = agent.resolve_memory_config(memory_config)
    resolved_system_tools_config = agent.resolve_system_tools_config(
        system_tools_config,
        allow_private_in_system_tools=allow_private_in_system_tools,
    )
    resolved_orchestration_config = agent.resolve_orchestration_config(orchestration_config)
    swarm = agent.get_swarm()
    swarm_scope = cast(_SwarmLike, swarm) if swarm is not None else None
    resolved_memory_assets_config = resolve_memory_assets_config(
        agent,
        memory_assets_config,
        default_agent_id=agent.id,
        default_swarm_id=swarm_scope.id if swarm_scope is not None else None,
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
        swarm_mode=(swarm_scope.hook_execution_mode if swarm_scope is not None else "tool"),
    )


def prepare_messages(
    messages: Sequence[BaseMessage],
    *,
    system_message: SystemMessage | None = None,
) -> list[BaseMessage]:
    """Prepare messages, injecting system message if needed."""
    messages_list = list(messages)
    has_system = any(isinstance(message, SystemMessage) for message in messages_list)
    if not has_system and system_message is not None:
        messages_list = [system_message, *messages_list]
    return messages_list


# MARK: Memory Assets


def build_memory_assets_config(
    agent: _AgentInvocationTarget,
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


def coerce_memory_assets_config(value: object) -> MemoryAssetsConfig | None:
    if value is None:
        return None
    if isinstance(value, MemoryAssetsConfig):
        return value
    if isinstance(value, dict):
        return MemoryAssetsConfig.model_validate(value)
    raise TypeError("memory_assets_config must be a MemoryAssetsConfig, dictionary, or None")


def resolve_memory_assets_config(
    agent: _AgentInvocationTarget,
    override: MemoryAssetsConfig | dict[str, object] | None = None,
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


def coerce_swarm_config(value: object) -> SwarmConfig | None:
    if value is None:
        return None
    if isinstance(value, SwarmConfig):
        return value
    if isinstance(value, dict):
        return SwarmConfig.model_validate(value)
    raise TypeError("swarm_config must be a SwarmConfig, dictionary, or None")


# MARK: Validation


def validate_invoke_params(
    agent: _AgentInvocationTarget,
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


def validate_final_tool_exists(agent: _AgentInvocationTarget) -> None:
    """Ensure at least one final_tool exists when force_final_tool is True."""
    all_tools = collect_all_tools(agent)
    final_tools = [tool for tool in all_tools if tool.final_tool]
    if not final_tools:
        raise ValueError(
            "force_final_tool=True requires at least one tool with final_tool=True. "
            + f"Agent '{agent.name}' has {len(all_tools)} tool(s) but none are final."
        )


def collect_all_tools(agent: _AgentInvocationTarget) -> list[BaseTool]:
    """Collect all tools from agent and parent swarm."""
    all_tools = list(agent.list_tools())
    swarm = agent.get_swarm()
    if swarm is not None:
        all_tools.extend(cast(_SwarmLike, swarm).list_tools())
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
