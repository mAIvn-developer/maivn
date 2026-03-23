"""Mixin for entities that require unique identification.

This mixin provides common UUID generation functionality to eliminate
DRY violations across different entity types.
"""

from __future__ import annotations

from typing import Any

from maivn_shared import create_uuid
from pydantic import BaseModel, Field

# MARK: - Base Identifiable


class IdentifiableMixin(BaseModel):
    """Mixin for entities that need unique identification."""

    id: str = Field(default="", description="Unique identifier")

    def model_post_init(self, __context: Any) -> None:
        """Initialize the identifier if not provided."""
        if not self.id:
            self.id = self._generate_id()
        super().model_post_init(__context)

    def _generate_id(self) -> str:
        """Generate a unique identifier.

        Override in subclasses for domain-specific ID generation.

        Returns:
            Generated unique identifier
        """
        return create_uuid(self)


# MARK: - Tool Identifiable


class ToolIdentifiableMixin(IdentifiableMixin):
    """Mixin for tool entities with content-based UUID generation."""

    tool_id: str = Field(default="", description="Unique tool identifier")

    def model_post_init(self, __context: Any) -> None:
        """Initialize the tool_id if not provided."""
        if not self.tool_id:
            self.tool_id = self._generate_tool_id()
        super().model_post_init(__context)

    def _generate_tool_id(self) -> str:
        """Generate a tool-specific identifier.

        Returns:
            Generated tool identifier
        """
        return create_uuid(self._get_id_source())

    def _get_id_source(self) -> Any:
        """Get the source object for ID generation.

        Override in subclasses to provide tool-specific source.

        Returns:
            Object to use for UUID generation
        """
        return self


# MARK: - Function Tool Identifiable


class FunctionToolIdentifiableMixin(ToolIdentifiableMixin):
    """Mixin for function-based tools with UUID generation."""

    def _get_id_source(self) -> Any:
        """Get the function for ID generation.

        Returns:
            Function object or self if unavailable
        """
        return getattr(self, "func", None) or self


# MARK: - Model Tool Identifiable


class ModelToolIdentifiableMixin(ToolIdentifiableMixin):
    """Mixin for model-based tools with UUID generation."""

    def _get_id_source(self) -> Any:
        """Get the model class for ID generation.

        Returns:
            Model class or self if unavailable
        """
        return getattr(self, "model", None) or self


# MARK: - Exports

__all__ = [
    "IdentifiableMixin",
    "ToolIdentifiableMixin",
    "FunctionToolIdentifiableMixin",
    "ModelToolIdentifiableMixin",
]
