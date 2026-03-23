"""Internal API implementations.
Not part of the public SDK API surface.
"""

from __future__ import annotations

# Import Agent and Swarm after BaseScope to handle dependencies
from .agent import Agent
from .base_scope import BaseScope
from .client import Client, ClientBuilder
from .mcp import MCPAutoSetup, MCPServer, MCPSoftErrorHandling
from .swarm import Swarm

__all__ = [
    "Agent",
    "BaseScope",
    "Client",
    "ClientBuilder",
    "MCPAutoSetup",
    "MCPServer",
    "MCPSoftErrorHandling",
    "Swarm",
]
