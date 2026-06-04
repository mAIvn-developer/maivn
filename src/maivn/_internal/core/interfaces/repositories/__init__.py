# pyright: strict
"""Repository interface definitions for maivn internals.
Defines protocol contracts for accessing agents, tools, and dependency data.
"""

from __future__ import annotations

# MARK: - Repository Interfaces
from .agent import AgentRepoInterface
from .dependency import DependencyRepoInterface
from .tool import ToolRepoInterface

# MARK: - Exports

__all__ = [
    "AgentRepoInterface",
    "DependencyRepoInterface",
    "ToolRepoInterface",
]
