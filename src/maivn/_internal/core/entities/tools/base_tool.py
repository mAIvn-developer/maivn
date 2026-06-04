"""Base tool entity shared by SDK tool implementations."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import Final

from maivn_shared import BaseDependency, ToolType
from pydantic import Field

from ..mixins import (
    ConfigurableMixin,
    DescriptiveMixin,
    TaggableMixin,
    ToolIdentifiableMixin,
)

# MARK: - Tool Type Constants

FUNCTION_TOOL_TYPE: Final[ToolType] = "func"
MODEL_TOOL_TYPE: Final[ToolType] = "model"
AGENT_TOOL_TYPE: Final[ToolType] = "agent"
MCP_TOOL_TYPE: Final[ToolType] = "mcp"
METHOD_TOOL_TYPE: Final[ToolType] = "method"


# MARK: - BaseTool


class BaseTool(
    ConfigurableMixin,
    ToolIdentifiableMixin,
    DescriptiveMixin,
    TaggableMixin,
):
    """Base model for all tools in the maivn package.

    Provides common metadata, dependency tracking, and hook fields for
    concrete tool entities.
    """

    # MARK: Fields

    dependencies: list[BaseDependency] = Field(
        default_factory=list,
        description="List of dependencies required by this tool",
    )
    always_execute: bool = Field(
        default=False,
        description="Flag to always execute this tool in every invocation",
    )
    final_tool: bool = Field(
        default=False,
        description="Flag indicating the tool's output is final",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional tool metadata used during compilation and planning",
    )

    before_execute: Callable[..., object] | None = Field(default=None)
    after_execute: Callable[..., object] | None = Field(default=None)

    # MARK: Dependency Management

    def add_dependency(self, dependency: BaseDependency) -> None:
        """Add a dependency to the tool.

        Args:
            dependency: Dependency to add
        """
        if dependency not in self.dependencies:
            self.dependencies.append(dependency)

    # MARK: Display Helpers

    @staticmethod
    def _callable_name(func: Callable[..., object], fallback: str) -> str:
        name = getattr(func, "__name__", fallback)
        return name if isinstance(name, str) else fallback

    def _format_tool_label(self, detail: str) -> str:
        return f"{self.name} ({detail})"


# MARK: - Exports

__all__ = [
    "BaseTool",
]
