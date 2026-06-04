"""Mixin classes for domain entities.
Provides reusable mixins shared across tools and other entity types."""

# pyright: strict
from __future__ import annotations

# MARK: - Configurable Mixins
from .configurable_mixin import (
    ConfigurableMixin,
    DescriptiveMixin,
    TaggableMixin,
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
    "TaggableMixin",
    # Identifiable mixins
    "FunctionToolIdentifiableMixin",
    "IdentifiableMixin",
    "ModelToolIdentifiableMixin",
    "ToolIdentifiableMixin",
]
