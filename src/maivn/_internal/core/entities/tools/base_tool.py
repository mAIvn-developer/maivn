"""Base tool entity with modular design.

This module provides the refactored base tool entity using mixins
to eliminate DRY violations and improve maintainability.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from maivn_shared import BaseDependency
from pydantic import Field

from ..mixins import (
    ConfigurableMixin,
    DescriptiveMixin,
    TaggableMixin,
    ToolIdentifiableMixin,
)

# MARK: - BaseTool


class BaseTool(
    ConfigurableMixin,
    ToolIdentifiableMixin,
    DescriptiveMixin,
    TaggableMixin,
):
    """Base model for all tools in the maivn package.

    Uses mixins to provide:
    - Configuration support (ConfigurableMixin)
    - Unique identification (ToolIdentifiableMixin)
    - Name and description (DescriptiveMixin)
    - Tagging support (TaggableMixin)
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
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional tool metadata used during compilation and planning",
    )
    tool_id: str = Field(
        default="",
        description="Unique tool identifier",
    )

    before_execute: Callable[[dict[str, Any]], Any] | None = Field(default=None)
    after_execute: Callable[[dict[str, Any]], Any] | None = Field(default=None)

    # MARK: Dependency Management

    def has_dependencies(self) -> bool:
        """Check if tool has any dependencies.

        Returns:
            True if tool has dependencies
        """
        return bool(self.dependencies)

    def add_dependency(self, dependency: BaseDependency) -> None:
        """Add a dependency to the tool.

        Args:
            dependency: Dependency to add
        """
        if dependency not in self.dependencies:
            self.dependencies.append(dependency)

    def remove_dependency(self, dependency: BaseDependency) -> None:
        """Remove a dependency from the tool.

        Args:
            dependency: Dependency to remove
        """
        if dependency in self.dependencies:
            self.dependencies.remove(dependency)

    # MARK: Execution

    def is_executable(self) -> bool:
        """Check if tool can be executed (has required implementation).

        This method should be overridden by subclasses.

        Returns:
            True if tool can be executed
        """
        return False


# MARK: - Exports

__all__ = [
    "BaseTool",
]
