"""Shared execution context for tool resolution and dependency handling."""

# pyright: strict
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TypeVar, cast

from maivn_shared import (
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SwarmConfig,
    SystemToolsConfig,
)

# MARK: - Types

OverrideT = TypeVar("OverrideT")


_MISSING = object()


def _override_value(
    overrides: Mapping[str, object],
    key: str,
    current: OverrideT,
) -> OverrideT:
    value = overrides.get(key, _MISSING)
    if value is _MISSING:
        return current
    return cast(OverrideT, value)


# MARK: - Execution Context


@dataclass(slots=True)
class ExecutionContext:
    """Typed container used across tool orchestration and domain services."""

    # MARK: - Fields

    scope: object | None = None
    timeout: float | None = None
    tool_results: dict[str, object] = field(default_factory=dict)
    messages: Iterable[object] | None = None
    metadata: Mapping[str, object] | None = None
    memory_config: MemoryConfig | None = None
    system_tools_config: SystemToolsConfig | None = None
    orchestration_config: SessionOrchestrationConfig | None = None
    memory_assets_config: MemoryAssetsConfig | None = None
    swarm_config: SwarmConfig | None = None

    # MARK: - Copy Methods

    def copy_with(self, **overrides: object) -> ExecutionContext:
        """Return a shallow copy with overrides applied."""
        return replace(
            self,
            scope=_override_value(overrides, "scope", self.scope),
            timeout=_override_value(overrides, "timeout", self.timeout),
            tool_results=_override_value(overrides, "tool_results", self.tool_results),
            messages=_override_value(overrides, "messages", self.messages),
            metadata=_override_value(overrides, "metadata", self.metadata),
            memory_config=_override_value(overrides, "memory_config", self.memory_config),
            system_tools_config=_override_value(
                overrides,
                "system_tools_config",
                self.system_tools_config,
            ),
            orchestration_config=_override_value(
                overrides,
                "orchestration_config",
                self.orchestration_config,
            ),
            memory_assets_config=_override_value(
                overrides,
                "memory_assets_config",
                self.memory_assets_config,
            ),
            swarm_config=_override_value(overrides, "swarm_config", self.swarm_config),
        )


__all__ = ["ExecutionContext"]
