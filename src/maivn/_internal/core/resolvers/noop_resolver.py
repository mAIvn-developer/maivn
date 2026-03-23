"""No-op scope resolver.
Default resolver that returns dependencies unchanged.
"""

from __future__ import annotations

from typing import Any

from maivn_shared import BaseDependency

from maivn._internal.core.interfaces.resolvers import ScopeResolverInterface

# MARK: - No-Op Resolver


class NoOpScopeResolver(ScopeResolverInterface):
    """Resolver that returns dependencies unchanged.

    Useful as a default to keep compile_tools() behavior consistent
    before wiring a real resolver implementation.
    """

    def set_context(self, *, scope: Any) -> None:
        """No context required for no-op behavior."""

    def resolve(self, dep: BaseDependency) -> BaseDependency:
        """Return the dependency unchanged."""
        return dep
