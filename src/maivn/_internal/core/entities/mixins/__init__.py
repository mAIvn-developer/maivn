"""Mixin classes for domain entities.
Provides reusable mixins shared across tools and other entity types."""

from __future__ import annotations

# MARK: - Configurable Mixins
from .configurable_mixin import (
    ConfigurableMixin,
    DescriptiveMixin,
    MetadataMixin,
    TaggableMixin,
    TimeStampedMixin,
)

# MARK: - Identifiable Mixins
from .identifiable_mixin import (
    FunctionToolIdentifiableMixin,
    IdentifiableMixin,
    ModelToolIdentifiableMixin,
    ToolIdentifiableMixin,
)

# MARK: - Exports

__all__ = [
    # Configurable mixins
    "ConfigurableMixin",
    "DescriptiveMixin",
    "MetadataMixin",
    "TaggableMixin",
    "TimeStampedMixin",
    # Identifiable mixins
    "FunctionToolIdentifiableMixin",
    "IdentifiableMixin",
    "ModelToolIdentifiableMixin",
    "ToolIdentifiableMixin",
]
