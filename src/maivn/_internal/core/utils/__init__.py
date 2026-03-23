"""Domain utility functions.
Contains small shared helpers used across the domain layer."""

from __future__ import annotations

# MARK: - Dependency Utilities
from .dependency_utils import normalize_dependencies

# MARK: - Exports

__all__ = [
    "normalize_dependencies",
]
