"""Repository interface definitions for maivn internals.
Defines protocol contracts for accessing agents, tools, and dependency data.
"""

from __future__ import annotations

from .agent import AgentRepoInterface
from .dependency import DependencyRepoInterface
from .tool import ToolRepoInterface

__all__ = [
    "AgentRepoInterface",
    "DependencyRepoInterface",
    "ToolRepoInterface",
]
