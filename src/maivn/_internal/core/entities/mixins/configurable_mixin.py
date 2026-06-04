"""Common Pydantic mixins for SDK entities."""

# pyright: strict
from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override

# MARK: - Configuration


class ConfigurableMixin(BaseModel):
    """Mixin for entities that support arbitrary types and validation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        populate_by_name=True,
    )


# MARK: - Descriptive


class DescriptiveMixin(BaseModel):
    """Mixin for entities that have names and descriptions."""

    name: str = Field(
        ...,
        description="Unique name using stable, identifier-friendly format",
    )
    description: str = Field(
        ...,
        description="Human-readable description explaining purpose and usage",
    )

    @override
    def __str__(self) -> str:
        """Return string representation using name."""
        return self.name

    @override
    def __repr__(self) -> str:
        """Return detailed representation."""
        return f"{self.__class__.__name__}(name='{self.name}')"


# MARK: - Taggable


class TaggableMixin(BaseModel):
    """Mixin for entities that support tagging/categorization."""

    tags: list[str] = Field(
        default_factory=list,
        description="List of tags for categorization and search",
    )


# MARK: - Exports

__all__ = [
    "ConfigurableMixin",
    "DescriptiveMixin",
    "TaggableMixin",
]
