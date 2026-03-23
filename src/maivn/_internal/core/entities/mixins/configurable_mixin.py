"""Mixin for entities with common configuration patterns.

This mixin provides reusable configuration functionality to eliminate
repeated field definitions and configuration patterns.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# MARK: - Configuration


class ConfigurableMixin(BaseModel):
    """Mixin for entities that support arbitrary types and validation."""

    model_config = ConfigDict(
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

    def __str__(self) -> str:
        """Return string representation using name."""
        return self.name

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

    def add_tag(self, tag: str) -> None:
        """Add a tag if not already present."""
        if tag not in self.tags:
            self.tags.append(tag)

    def remove_tag(self, tag: str) -> None:
        """Remove a tag if present."""
        if tag in self.tags:
            self.tags.remove(tag)

    def has_tag(self, tag: str) -> bool:
        """Check if entity has a specific tag."""
        return tag in self.tags

    def has_any_tags(self, tags: list[str]) -> bool:
        """Check if entity has any of the specified tags."""
        return any(tag in self.tags for tag in tags)


# MARK: - Metadata


class MetadataMixin(BaseModel):
    """Mixin for entities that support arbitrary metadata."""

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata for extensibility",
    )

    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata value."""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value."""
        return self.metadata.get(key, default)

    def has_metadata(self, key: str) -> bool:
        """Check if metadata key exists."""
        return key in self.metadata


# MARK: - TimeStamped


class TimeStampedMixin(BaseModel):
    """Mixin for entities that track creation and modification times."""

    created_at: float | None = Field(
        default=None,
        description="Unix timestamp of creation",
    )
    updated_at: float | None = Field(
        default=None,
        description="Unix timestamp of last update",
    )

    def model_post_init(self, __context: Any) -> None:
        """Initialize timestamps if not provided."""
        import time

        current_time = time.time()
        if self.created_at is None:
            self.created_at = current_time
        if self.updated_at is None:
            self.updated_at = current_time
        super().model_post_init(__context)

    def touch(self) -> None:
        """Update the updated_at timestamp to current time."""
        import time

        self.updated_at = time.time()


# MARK: - Exports

__all__ = [
    "ConfigurableMixin",
    "DescriptiveMixin",
    "MetadataMixin",
    "TaggableMixin",
    "TimeStampedMixin",
]
