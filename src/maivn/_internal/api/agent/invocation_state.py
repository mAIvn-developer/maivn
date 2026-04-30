"""Invocation state shared by Agent call paths and hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from maivn_shared import (
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SwarmConfig,
    SystemToolsConfig,
)

if TYPE_CHECKING:
    from ..swarm import Swarm


# MARK: Invocation State


@dataclass(frozen=True)
class InvocationState:
    prepared_messages: list[BaseMessage]
    merged_metadata: dict[str, Any]
    resolved_memory_config: MemoryConfig | None
    resolved_system_tools_config: SystemToolsConfig | None
    resolved_orchestration_config: SessionOrchestrationConfig | None
    resolved_memory_assets_config: MemoryAssetsConfig | None
    resolved_swarm_config: SwarmConfig | None
    swarm: Swarm | None
    agent_mode: str
    swarm_mode: str


__all__ = ["InvocationState"]
