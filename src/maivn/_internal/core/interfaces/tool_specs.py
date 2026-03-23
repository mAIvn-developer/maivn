"""Tool specification provider interface.
Defines the protocol for components that can list available ToolSpecs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from maivn_shared import ToolSpec

# MARK: - Tool Specification Provider Interface


class ToolSpecProvider(Protocol):
    """Protocol describing a provider that returns available tool specifications."""

    def list_tool_specs(self) -> Sequence[ToolSpec]:
        """Return the collection of tool specifications available to the orchestrator."""
        ...
