# pyright: strict
"""Scope resolver implementations.
Provides default resolvers used during state compilation and tool wiring.
"""

from __future__ import annotations

# MARK: - Scope Resolvers
from .noop_resolver import NoOpScopeResolver

# MARK: - Exports

__all__ = [
    "NoOpScopeResolver",
]
