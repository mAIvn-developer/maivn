"""Domain entities for the maivn SDK internals.
Includes session/SSE models, tool representations, registries, and execution context.
Used by state compilation and orchestration.
"""

# pyright: strict
from __future__ import annotations

# MARK: - Configuration
from .execution_context import ExecutionContext

# MARK: - Session
from .session_endpoints import SessionEndpoints
from .sse_event import SSEEvent
from .state_compilation_config import StateCompilationConfig

# MARK: - Tool Events
from .tool_events import (
    ToolCallPayload,
    ToolEventPayload,
    ToolEventValue,
    UpdateEventPayload,
)

# MARK: - Tools
from .tools import AgentTool, BaseTool, FunctionTool, McpTool, MethodTool, ModelTool

# MARK: - Exports

__all__ = [
    # Session
    "SessionEndpoints",
    "SSEEvent",
    # Configuration
    "ExecutionContext",
    "StateCompilationConfig",
    # Tool Events
    "ToolCallPayload",
    "ToolEventPayload",
    "ToolEventValue",
    "UpdateEventPayload",
    # Tools
    "AgentTool",
    "BaseTool",
    "FunctionTool",
    "McpTool",
    "MethodTool",
    "ModelTool",
]
