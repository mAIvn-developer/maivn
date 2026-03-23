"""Registration helpers for maivn scopes.
Provides registrars for agents, tools, and dependency metadata.
"""

from __future__ import annotations

# MARK: - Registrar Services
from .register_agent import AgentRegistrar
from .register_dependency import DependencyRegistrar
from .register_tools import ToolRegistrar

__all__ = [
    "AgentRegistrar",
    "DependencyRegistrar",
    "ToolRegistrar",
]
