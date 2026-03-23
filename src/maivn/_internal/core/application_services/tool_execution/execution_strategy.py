"""Execution strategies for different tool types.

This module implements the Strategy pattern for tool execution, allowing
type-specific execution logic to be encapsulated and dispatched cleanly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import BaseTool, FunctionTool, McpTool, ModelTool
from maivn._internal.core.exceptions import ToolExecutionError

if TYPE_CHECKING:
    from maivn._internal.core.entities.execution_context import ExecutionContext

    from ..helpers import PydanticDeserializer


# MARK: Protocol


@runtime_checkable
class ToolExecutionStrategy(Protocol):
    """Protocol for tool execution strategies.

    Each strategy handles execution for a specific tool type, encapsulating
    the type-specific logic needed to invoke the tool.
    """

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool.

        Args:
            tool: Tool to check

        Returns:
            True if this strategy handles this tool type
        """
        ...

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> Any:
        """Execute the tool with the given arguments.

        Args:
            tool: Tool to execute
            args: Arguments for execution
            context: Optional execution context

        Returns:
            Tool execution result
        """
        ...


# MARK: Function Strategy


class FunctionExecutionStrategy:
    """Strategy for executing function tools."""

    def __init__(
        self,
        *,
        logger: LoggerProtocol | None = None,
        deserializer: PydanticDeserializer | None = None,
    ) -> None:
        """Initialize function execution strategy.

        Args:
            logger: Logger for operation tracking
            deserializer: Pydantic deserializer for argument conversion
        """
        self._logger = logger
        self._deserializer = deserializer

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool."""
        return isinstance(tool, FunctionTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> Any:
        """Execute a function tool.

        Args:
            tool: Function tool to execute
            args: Arguments for execution
            context: Execution context (unused for function tools)

        Returns:
            Function execution result

        Raises:
            ToolExecutionError: If tool has no callable function
        """
        if not isinstance(tool, FunctionTool):
            raise ToolExecutionError(
                tool_id=getattr(tool, "name", "unknown"),
                reason=f"Expected FunctionTool, got {type(tool).__name__}",
            )

        func = getattr(tool, "func", None)
        if not callable(func):
            raise ToolExecutionError(
                tool_id=getattr(tool, "name", "unknown"),
                reason="FunctionTool has no callable 'func'",
            )

        if self._logger:
            self._logger.info("[TOOL_EXEC] Executing function %s", func.__name__)

        # Deserialize dict arguments to Pydantic models if deserializer provided
        if self._deserializer:
            args = self._deserializer.deserialize_args(func, args)

        return func(**args)


# MARK: Model Strategy


class ModelExecutionStrategy:
    """Strategy for executing model tools (Pydantic models)."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        """Initialize model execution strategy.

        Args:
            logger: Logger for operation tracking
        """
        self._logger = logger

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool."""
        return isinstance(tool, ModelTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> Any:
        """Execute a model tool.

        Args:
            tool: Model tool to execute
            args: Arguments for execution
            context: Execution context (unused for model tools)

        Returns:
            Model execution result (dict representation)

        Raises:
            ToolExecutionError: If tool has no model
        """
        if not isinstance(tool, ModelTool):
            raise ToolExecutionError(
                tool_id=getattr(tool, "name", "unknown"),
                reason=f"Expected ModelTool, got {type(tool).__name__}",
            )

        model_cls = getattr(tool, "model", None)
        if model_cls is None:
            raise ToolExecutionError(
                tool_id=getattr(tool, "name", "unknown"),
                reason="ModelTool has no 'model' attribute",
            )

        if self._logger:
            self._logger.info("[TOOL_EXEC] Executing model %s", model_cls.__name__)

        try:
            instance = model_cls(**args)
            return instance.model_dump(mode="json")
        except Exception as e:
            raise ToolExecutionError(
                tool_id=model_cls.__name__,
                reason=f"Model validation failed: {e}",
                original_error=e,
            ) from e


# MARK: MCP Strategy


class McpExecutionStrategy:
    """Strategy for executing MCP tools."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        """Initialize MCP execution strategy.

        Args:
            logger: Logger for operation tracking
        """
        self._logger = logger

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool."""
        return isinstance(tool, McpTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> Any:
        """Execute an MCP tool.

        Args:
            tool: MCP tool to execute
            args: Arguments for execution
            context: Execution context for server lookup

        Returns:
            MCP tool execution result

        Raises:
            ToolExecutionError: If MCP server not found
        """
        if not isinstance(tool, McpTool):
            raise ToolExecutionError(
                tool_id=getattr(tool, "name", "unknown"),
                reason=f"Expected McpTool, got {type(tool).__name__}",
            )

        server = self._resolve_server(tool, context)

        if self._logger:
            self._logger.info(
                "[TOOL_EXEC] Executing MCP tool %s via %s",
                tool.mcp_tool_name,
                tool.server_name,
            )

        try:
            return server.call_tool(tool.mcp_tool_name, args)
        except Exception as exc:
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP tool execution failed: {exc}",
                original_error=exc,
            ) from exc

    def _resolve_server(self, tool: McpTool, context: ExecutionContext | None) -> Any:
        """Resolve the MCP server for the tool.

        Args:
            tool: MCP tool to resolve server for
            context: Execution context

        Returns:
            MCP server instance

        Raises:
            ToolExecutionError: If server not found
        """
        server = getattr(tool, "server", None)

        if server is None and context is not None:
            scope = getattr(context, "scope", None)
            if scope:
                servers = getattr(scope, "_mcp_servers", {})
                server = servers.get(tool.server_name)

        if server is None:
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP server '{tool.server_name}' not found",
            )

        return server


# MARK: Strategy Registry


class StrategyRegistry:
    """Registry for tool execution strategies.

    Manages a collection of strategies and dispatches execution
    to the appropriate strategy based on tool type.
    """

    def __init__(self, strategies: list[ToolExecutionStrategy] | None = None) -> None:
        """Initialize strategy registry.

        Args:
            strategies: Initial list of strategies (order matters for dispatch)
        """
        self._strategies: list[ToolExecutionStrategy] = list(strategies or [])

    def register(self, strategy: ToolExecutionStrategy) -> None:
        """Register a new execution strategy.

        Args:
            strategy: Strategy to register
        """
        self._strategies.append(strategy)

    def get_strategy(self, tool: BaseTool) -> ToolExecutionStrategy | None:
        """Get the strategy that can execute the given tool.

        Args:
            tool: Tool to find strategy for

        Returns:
            Strategy that can execute the tool, or None
        """
        for strategy in self._strategies:
            if strategy.can_execute(tool):
                return strategy
        return None

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> Any:
        """Execute a tool using the appropriate strategy.

        Args:
            tool: Tool to execute
            args: Arguments for execution
            context: Execution context

        Returns:
            Tool execution result

        Raises:
            ToolExecutionError: If no strategy found for tool type
        """
        strategy = self.get_strategy(tool)
        if strategy is None:
            raise ToolExecutionError(
                tool_id=getattr(tool, "name", "unknown"),
                reason=f"No execution strategy for tool type: {type(tool).__name__}",
            )
        return strategy.execute(tool, args, context)


# MARK: Factory


def create_default_registry(
    *,
    logger: LoggerProtocol | None = None,
    deserializer: PydanticDeserializer | None = None,
) -> StrategyRegistry:
    """Create a registry with default execution strategies.

    Args:
        logger: Logger for operation tracking
        deserializer: Pydantic deserializer for function tools

    Returns:
        Registry with function, model, and MCP strategies
    """
    return StrategyRegistry(
        [
            FunctionExecutionStrategy(logger=logger, deserializer=deserializer),
            ModelExecutionStrategy(logger=logger),
            McpExecutionStrategy(logger=logger),
        ]
    )


__all__ = [
    "FunctionExecutionStrategy",
    "McpExecutionStrategy",
    "ModelExecutionStrategy",
    "StrategyRegistry",
    "ToolExecutionStrategy",
    "create_default_registry",
]
