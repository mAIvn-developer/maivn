"""Scope resolver implementations.
Provides default resolvers used during state compilation and tool wiring.
"""

from __future__ import annotations

from .noop_resolver import NoOpScopeResolver

__all__ = [
    "NoOpScopeResolver",
]
