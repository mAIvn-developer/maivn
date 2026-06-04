"""Execution strategies for different tool types.

This module implements the Strategy pattern for tool execution, allowing
type-specific execution logic to be encapsulated and dispatched cleanly.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import BaseTool, FunctionTool, McpTool, ModelTool
from maivn._internal.core.entities.tools import MethodTool
from maivn._internal.core.exceptions import ToolExecutionError

if TYPE_CHECKING:
    from maivn._internal.core.entities.execution_context import ExecutionContext

    from ..helpers import PydanticDeserializer


# MARK: Protocols


class McpServerProtocol(Protocol):
    """MCP server surface required by execution strategies."""

    def call_tool(self, tool_name: str, args: dict[str, object]) -> object:
        """Call an MCP tool by name."""
        ...


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
        args: dict[str, object],
        context: ExecutionContext | None = None,
    ) -> object:
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
        self._logger: LoggerProtocol | None = logger
        self._deserializer: PydanticDeserializer | None = deserializer

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool."""
        return isinstance(tool, FunctionTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, object],
        context: ExecutionContext | None = None,
    ) -> object:
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
                tool_id=tool.name,
                reason=f"Expected FunctionTool, got {type(tool).__name__}",
            )

        _ = context
        func = tool.func

        if self._logger:
            self._logger.info("[TOOL_EXEC] Executing function %s", _callable_name(func))

        # Deserialize dict arguments to Pydantic models if deserializer provided
        if self._deserializer:
            args = self._deserializer.deserialize_args(func, args)

        return func(**args)


# MARK: Method Strategy


class MethodExecutionStrategy:
    """Strategy for executing method tools.

    A ``MethodTool`` wraps a bound method on a connector/toolset instance
    (typically produced by ``@toolset`` + ``@toolify``). Execution is
    structurally identical to a function tool: ``tool.func`` is a callable
    that already closes over its host instance, so the strategy just
    invokes it with the LLM-supplied kwargs.
    """

    def __init__(
        self,
        *,
        logger: LoggerProtocol | None = None,
        deserializer: PydanticDeserializer | None = None,
    ) -> None:
        self._logger: LoggerProtocol | None = logger
        self._deserializer: PydanticDeserializer | None = deserializer

    def can_execute(self, tool: BaseTool) -> bool:
        return isinstance(tool, MethodTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, object],
        context: ExecutionContext | None = None,
    ) -> object:
        if not isinstance(tool, MethodTool):
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"Expected MethodTool, got {type(tool).__name__}",
            )

        _ = context
        func = tool.func

        if self._logger:
            self._logger.info(
                "[TOOL_EXEC] Executing method %s",
                _callable_name(func, fallback="<method>"),
            )

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
        self._logger: LoggerProtocol | None = logger

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool."""
        return isinstance(tool, ModelTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, object],
        context: ExecutionContext | None = None,
    ) -> object:
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
                tool_id=tool.name,
                reason=f"Expected ModelTool, got {type(tool).__name__}",
            )

        _ = context
        model_cls = tool.model

        if self._logger:
            self._logger.info("[TOOL_EXEC] Executing model %s", model_cls.__name__)

        try:
            instance = model_cls(**args)
            return instance.model_dump(mode="json")
        except Exception as e:  # noqa: BLE001 - model validation is reported as tool failure
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
        self._logger: LoggerProtocol | None = logger

    def can_execute(self, tool: BaseTool) -> bool:
        """Check if this strategy can execute the given tool."""
        return isinstance(tool, McpTool)

    def execute(
        self,
        tool: BaseTool,
        args: dict[str, object],
        context: ExecutionContext | None = None,
    ) -> object:
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
                tool_id=tool.name,
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
        except Exception as exc:  # noqa: BLE001 - MCP server failures are reported as tool failure
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP tool execution failed: {exc}",
                original_error=exc,
            ) from exc

    def _resolve_server(self, tool: McpTool, context: ExecutionContext | None) -> McpServerProtocol:
        """Resolve the MCP server for the tool.

        Args:
            tool: MCP tool to resolve server for
            context: Execution context

        Returns:
            MCP server instance

        Raises:
            ToolExecutionError: If server not found
        """
        server = cast(McpServerProtocol | None, tool.server)

        if server is None and context is not None:
            servers = _mcp_servers_from_scope(context.scope)
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
        args: dict[str, object],
        context: ExecutionContext | None = None,
    ) -> object:
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
                tool_id=tool.name,
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
            MethodExecutionStrategy(logger=logger, deserializer=deserializer),
            ModelExecutionStrategy(logger=logger),
            McpExecutionStrategy(logger=logger),
        ]
    )


# MARK: Helpers


def _callable_name(func: object, *, fallback: str = "<function>") -> str:
    name = cast(object, getattr(func, "__name__", fallback))
    return name if isinstance(name, str) and name else fallback


def _mcp_servers_from_scope(scope: object | None) -> Mapping[str, McpServerProtocol]:
    servers = cast(object, getattr(scope, "_mcp_servers", {}))
    if isinstance(servers, Mapping):
        return cast(Mapping[str, McpServerProtocol], servers)
    return {}


__all__ = [
    "FunctionExecutionStrategy",
    "McpExecutionStrategy",
    "MethodExecutionStrategy",
    "ModelExecutionStrategy",
    "StrategyRegistry",
    "ToolExecutionStrategy",
    "create_default_registry",
]
