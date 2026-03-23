"""Dependency resolver interfaces.
Defines protocols for resolving dependencies within a scope context.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from maivn_shared import BaseDependency

# MARK: Resolver Interfaces


# MARK: - Dependency Resolver Interface


class DependencyResolverInterface(ABC):
    """Resolve a single dependency within a given scope context."""

    @abstractmethod
    def resolve(self, dep: BaseDependency) -> BaseDependency:
        """Return a resolved dependency (may be the same instance).

        Implementations should convert reference-only dependencies into
        fully bound ones (e.g., resolving tool/agent/data references).
        """
        raise NotImplementedError


# MARK: - Scope Resolver Interface


class ScopeResolverInterface(DependencyResolverInterface, ABC):
    """A dependency resolver with optional access to the current scope context."""

    @abstractmethod
    def set_context(self, *, scope: Any) -> None:
        """Attach a scope context to the resolver if needed."""
        raise NotImplementedError
