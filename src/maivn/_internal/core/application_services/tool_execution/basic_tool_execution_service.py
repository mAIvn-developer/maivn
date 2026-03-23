"""Basic tool execution service with index management and direct dispatch.

Provides ``BasicToolExecutionService`` which handles tool registration,
lookup, and simple execute-by-id without dependency resolution or hooks.
"""

from __future__ import annotations

import time
from typing import Any

from maivn_shared import to_jsonable as _shared_to_jsonable
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol

from maivn._internal.core.entities import FunctionTool, McpTool, ModelTool
from maivn._internal.utils.logging import get_optional_logger

ToolType = FunctionTool | ModelTool | McpTool


# MARK: Basic Execution Service


class BasicToolExecutionService:
    """Executes tools and manages tooling indexes for orchestrators."""

    def __init__(self, *, logger: MetricsLoggerProtocol | None = None) -> None:
        self._logger: MetricsLoggerProtocol = logger or get_optional_logger()
        self._tool_index: dict[str, ToolType] = {}

    # MARK: - Index Management

    def rebuild_index(self, tools: list[ToolType]) -> None:
        """Rebuild the lookup index from the provided tools."""
        self._tool_index.clear()

        for tool in tools:
            self._register_tool(tool)

    def _register_tool(self, tool: ToolType) -> None:
        """Register a single tool with all its identifiers."""
        tool_id = getattr(tool, "tool_id", None) or getattr(tool, "id", None)
        if tool_id:
            self._register_identifier(tool_id, tool)

        name = getattr(tool, "name", None)
        if name:
            self._register_identifier(name, tool)

        if getattr(tool, "tool_type", None) == "agent":
            agent_id = getattr(tool, "target_agent_id", None) or getattr(tool, "agent_id", None)
            if agent_id:
                self._register_identifier(agent_id, tool)

    def _register_identifier(self, identifier: str, tool: ToolType) -> None:
        """Register a tool under a specific identifier."""
        key = str(identifier)
        existing = self._tool_index.get(key)
        if existing is not None and existing is not tool:
            raise ValueError(
                f"Duplicate tool identifier '{key}' detected between "
                f"{getattr(existing, 'name', repr(existing))} and "
                f"{getattr(tool, 'name', repr(tool))}."
            )
        self._tool_index[key] = tool

    def resolve_tool(self, tool_id: str) -> ToolType:
        """Resolve a registered tool by id or name."""
        tool = self._tool_index.get(tool_id) or self._tool_index.get(tool_id.strip())
        if tool is None:
            raise ValueError(f"Tool not found: {tool_id}")
        return tool

    # MARK: - Execution

    def execute_tool_call(self, tool_id: str, args: dict[str, Any]) -> Any:
        """Execute a tool call by id with the provided args."""
        tool = self.resolve_tool(tool_id)
        tool_name = getattr(tool, "name", tool_id)
        tool_type = self._get_tool_type_name(tool)

        start_time = time.time()
        self._log_execution_start(tool_id, tool_name, tool_type, args)

        try:
            result = self._execute_tool(tool, args)
            self._log_execution_success(tool_id, tool_name, tool_type, result, start_time)
            return result
        except Exception as e:
            self._log_execution_failure(tool_id, tool_name, tool_type, e, start_time)
            raise

    def _get_tool_type_name(self, tool: ToolType) -> str:
        """Get the type name for a tool."""
        if isinstance(tool, FunctionTool):
            return "FUNCTION"
        if isinstance(tool, ModelTool):
            return "MODEL"
        if isinstance(tool, McpTool):
            return "MCP"
        return "UNKNOWN"

    def _execute_tool(self, tool: ToolType, args: dict[str, Any]) -> Any:
        """Execute a tool and return the result."""
        if isinstance(tool, FunctionTool):
            return self._execute_function_tool(tool, args)
        if isinstance(tool, ModelTool):
            return self._execute_model_tool(tool, args)
        if isinstance(tool, McpTool):
            server = getattr(tool, "server", None)
            if server is None:
                raise TypeError("McpTool has no MCP server reference")
            return server.call_tool(tool.mcp_tool_name, args)
        raise TypeError(f"Unsupported tool type: {type(tool).__name__}")

    def _execute_function_tool(self, tool: FunctionTool, args: dict[str, Any]) -> Any:
        """Execute a function tool."""
        func = getattr(tool, "func", None)
        if not callable(func):
            raise TypeError("FunctionTool has no callable 'func'")
        return func(**args)

    def _execute_model_tool(self, tool: ModelTool, args: dict[str, Any]) -> Any:
        """Execute a model tool."""
        model_cls = getattr(tool, "model", None)
        if model_cls is None:
            raise TypeError("ModelTool has no 'model'")
        instance = model_cls(**args)
        return instance.model_dump(mode="json")

    # MARK: - Logging

    def _log_execution_start(
        self, tool_id: str, tool_name: str, tool_type: str, args: dict[str, Any]
    ) -> None:
        """Log the start of tool execution."""
        self._logger.log_tool_execution(
            phase="start",
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=tool_type,
            args=args,
        )

    def _log_execution_success(
        self,
        tool_id: str,
        tool_name: str,
        tool_type: str,
        result: Any,
        start_time: float,
    ) -> None:
        """Log successful tool execution."""
        elapsed_ms = int((time.time() - start_time) * 1000)
        self._logger.log_tool_execution(
            phase="completed",
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=tool_type,
            result=result,
            elapsed_ms=elapsed_ms,
        )

    def _log_execution_failure(
        self,
        tool_id: str,
        tool_name: str,
        tool_type: str,
        error: Exception,
        start_time: float,
    ) -> None:
        """Log failed tool execution."""
        elapsed_ms = int((time.time() - start_time) * 1000)
        self._logger.log_tool_execution(
            phase="failed",
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=tool_type,
            error=str(error),
            elapsed_ms=elapsed_ms,
        )

    # MARK: - Serialization

    def to_jsonable(self, obj: Any) -> Any:
        """Convert a result to a JSON-serializable structure."""
        return _shared_to_jsonable(obj)


__all__ = ["BasicToolExecutionService", "ToolType"]
