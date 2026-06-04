"""Mixins for entities that require stable identifiers."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from maivn_shared import create_uuid
from pydantic import BaseModel, Field
from typing_extensions import override

# MARK: - Host Protocols


class FunctionToolIdentityHost(Protocol):
    """Host contract required by FunctionToolIdentifiableMixin."""

    func: Callable[..., object]


class ModelToolIdentityHost(Protocol):
    """Host contract required by ModelToolIdentifiableMixin."""

    model: type[BaseModel]


# MARK: - Base Identifiable


class IdentifiableMixin(BaseModel):
    """Mixin for entities that need unique identification."""

    id: str = Field(default="", description="Unique identifier")

    @override
    def model_post_init(self, __context: object) -> None:
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

    @override
    def model_post_init(self, __context: object) -> None:
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

    def _get_id_source(self) -> object:
        """Get the source object for ID generation.

        Override in subclasses to provide tool-specific source.

        Returns:
            Object to use for UUID generation
        """
        return self


# MARK: - Function Tool Identifiable


class FunctionToolIdentifiableMixin(ToolIdentifiableMixin):
    """Mixin for function-based tools with UUID generation."""

    @override
    def _get_id_source(self) -> object:
        """Get the function for ID generation."""
        return cast(FunctionToolIdentityHost, cast(object, self)).func


# MARK: - Model Tool Identifiable


class ModelToolIdentifiableMixin(ToolIdentifiableMixin):
    """Mixin for model-based tools with UUID generation."""

    @override
    def _get_id_source(self) -> object:
        """Get the model class for ID generation."""
        return cast(ModelToolIdentityHost, cast(object, self)).model


# MARK: - Exports

__all__ = [
    "IdentifiableMixin",
    "ToolIdentifiableMixin",
    "FunctionToolIdentifiableMixin",
    "ModelToolIdentifiableMixin",
]
