# pyright: strict
"""No-op scope resolver.
Default resolver that returns dependencies unchanged.
"""

from __future__ import annotations

from maivn_shared import BaseDependency
from typing_extensions import override

from ..interfaces.resolvers import ScopeResolverInterface

# MARK: - No-Op Resolver


class NoOpScopeResolver(ScopeResolverInterface):
    """Resolver that returns dependencies unchanged.

    Useful as a default to keep state-compilation dependency resolution
    behavior consistent before wiring a real resolver implementation.
    """

    @override
    def set_context(self, *, scope: object) -> None:
        """No context required for no-op behavior."""

    @override
    def resolve(self, dep: BaseDependency) -> BaseDependency:
        """Return the dependency unchanged."""
        return dep
