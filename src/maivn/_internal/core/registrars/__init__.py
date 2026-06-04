# pyright: strict
"""Registration helpers for maivn scopes.
Provides registrars for agents and tools.
"""

from __future__ import annotations

# MARK: - Registrar Services
from .register_agent import AgentRegistrar
from .register_tools import ToolRegistrar

__all__ = [
    "AgentRegistrar",
    "ToolRegistrar",
]
