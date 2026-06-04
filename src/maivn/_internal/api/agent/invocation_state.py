"""Invocation state shared by Agent call paths and hooks."""

# pyright: strict
from __future__ import annotations

from dataclasses import dataclass

from maivn_shared import (
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SwarmConfig,
    SystemToolsConfig,
)

from maivn._internal.core.interfaces.orchestrator_protocol import JsonObject

# MARK: Invocation State


@dataclass(frozen=True)
class InvocationState:
    prepared_messages: list[BaseMessage]
    merged_metadata: JsonObject
    resolved_memory_config: MemoryConfig | None
    resolved_system_tools_config: SystemToolsConfig | None
    resolved_orchestration_config: SessionOrchestrationConfig | None
    resolved_memory_assets_config: MemoryAssetsConfig | None
    resolved_swarm_config: SwarmConfig | None
    swarm: object | None
    agent_mode: str
    swarm_mode: str


__all__ = ["InvocationState"]
