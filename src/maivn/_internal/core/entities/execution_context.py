"""Shared execution context for tool resolution and dependency handling."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from maivn_shared import MemoryConfig

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
        }


__all__ = ["ExecutionContext"]
