# pyright: strict
"""Protocol and interface definitions for maivn internals.
Defines the orchestrator and SSE client contracts.
"""

from __future__ import annotations

# MARK: - Protocol Interfaces
from .orchestrator_protocol import AgentOrchestratorInterface
from .sse_client import SSEClient

# MARK: - Exports

__all__ = [
    "AgentOrchestratorInterface",
    "SSEClient",
]
