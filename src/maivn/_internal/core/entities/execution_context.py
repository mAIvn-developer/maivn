"""Shared execution context for tool resolution and dependency handling."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from maivn_shared import (
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SwarmConfig,
    SystemToolsConfig,
)

# MARK: - Execution Context


@dataclass(slots=True)
class ExecutionContext:
    """Typed container used across tool orchestration and domain services."""

    # MARK: - Fields

    scope: Any | None = None
    timeout: float | None = None
    tool_results: dict[str, Any] = field(default_factory=dict)
    messages: Iterable[Any] | None = None
    metadata: Mapping[str, Any] | None = None
    memory_config: MemoryConfig | None = None
    system_tools_config: SystemToolsConfig | None = None
    orchestration_config: SessionOrchestrationConfig | None = None
    memory_assets_config: MemoryAssetsConfig | None = None
    swarm_config: SwarmConfig | None = None

    # MARK: - Copy Methods

    def copy_with(self, **overrides: Any) -> ExecutionContext:
        """Return a shallow copy with overrides applied."""
        return replace(
            self,
            scope=overrides.get("scope", self.scope),
            timeout=overrides.get("timeout", self.timeout),
            tool_results=overrides.get("tool_results", self.tool_results),
            messages=overrides.get("messages", self.messages),
            metadata=overrides.get("metadata", self.metadata),
            memory_config=overrides.get("memory_config", self.memory_config),
            system_tools_config=overrides.get(
                "system_tools_config",
                self.system_tools_config,
            ),
            orchestration_config=overrides.get(
                "orchestration_config",
                self.orchestration_config,
            ),
            memory_assets_config=overrides.get(
                "memory_assets_config",
                self.memory_assets_config,
            ),
            swarm_config=overrides.get("swarm_config", self.swarm_config),
        )

    # MARK: - Serialization

    def as_dict(self) -> dict[str, Any]:
        """Expose a dict view for legacy call-sites."""
        return {
            "scope": self.scope,
            "timeout": self.timeout,
            "tool_results": self.tool_results,
            "messages": self.messages,
            "metadata": self.metadata,
            "memory_config": self.memory_config,
            "system_tools_config": self.system_tools_config,
            "orchestration_config": self.orchestration_config,
            "memory_assets_config": self.memory_assets_config,
            "swarm_config": self.swarm_config,
        }


__all__ = ["ExecutionContext"]
