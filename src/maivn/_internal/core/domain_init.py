"""Domain models and interfaces for the maivn SDK internals.
Re-exports core entities (SSE/session/tool event payloads) and protocol interfaces.
Internal-only API used by orchestrators and infrastructure.
"""

from __future__ import annotations

# MARK: - Entities
from .entities import (
    SessionEndpoints,
    SSEEvent,
    ToolCallPayload,
    ToolEventPayload,
    ToolEventValue,
    UpdateEventPayload,
)

# MARK: - Interfaces
from .interfaces import SSEClient, ToolExecutor, ToolSpecProvider

# MARK: - Exports

__all__ = [
    # Entities
    "SessionEndpoints",
    "SSEEvent",
    "ToolCallPayload",
    "ToolEventPayload",
    "ToolEventValue",
    "UpdateEventPayload",
    # Interfaces
    "SSEClient",
    "ToolExecutor",
    "ToolSpecProvider",
]
