"""Basic tool execution service with index management and direct dispatch.

Provides ``BasicToolExecutionService`` which handles tool registration,
lookup, and simple execute-by-id without dependency resolution or hooks.
"""

# pyright: strict
from __future__ import annotations

import inspect
import time
from collections.abc import Callable
from typing import Protocol, cast, get_type_hints

from maivn_shared import to_jsonable as _shared_to_jsonable
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol
from pydantic import JsonValue, TypeAdapter

from maivn._internal.core.entities import FunctionTool, McpTool, ModelTool
from maivn._internal.core.entities.tools import MethodTool
from maivn._internal.core.exceptions import ToolExecutionError
from maivn._internal.utils.logging import get_optional_logger

ToolType = FunctionTool | MethodTool | ModelTool | McpTool
CallableObject = Callable[..., object]


# MARK: Protocols


class McpServerProtocol(Protocol):
    """MCP server surface required for direct basic execution."""

    def call_tool(self, tool_name: str, args: dict[str, object]) -> object:
        """Call a tool on the MCP server."""
        ...


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
        tool_id = tool.tool_id or tool.id
        if tool_id:
            self._register_identifier(tool_id, tool)

        if tool.name:
            self._register_identifier(tool.name, tool)

        if tool.tool_type == "agent":
            agent_id = _string_attr(tool, "target_agent_id") or _string_attr(tool, "agent_id")
            if agent_id:
                self._register_identifier(agent_id, tool)

    def _register_identifier(self, identifier: str, tool: ToolType) -> None:
        """Register a tool under a specific identifier."""
        key = str(identifier)
        existing = self._tool_index.get(key)
        if existing is not None and existing is not tool:
            message = (
                f"Duplicate tool identifier '{key}' detected between "
                f"{existing.name} and {tool.name}."
            )
            raise ValueError(message)
        self._tool_index[key] = tool

    def resolve_tool(self, tool_id: str) -> ToolType:
        """Resolve a registered tool by id or name."""
        tool = self._tool_index.get(tool_id) or self._tool_index.get(tool_id.strip())
        if tool is None:
            raise ValueError(f"Tool not found: {tool_id}")
        return tool

    # MARK: - Execution

    def execute_tool_call(self, tool_id: str, args: dict[str, JsonValue]) -> object:
        """Execute a tool call by id with the provided args."""
        tool = self.resolve_tool(tool_id)
        tool_name = getattr(tool, "name", tool_id)
        tool_type = self._get_tool_type_name(tool)
        execution_args: dict[str, object] = dict(args)

        start_time = time.time()
        self._log_execution_start(tool_id, tool_name, tool_type, execution_args)

        try:
            result = self._execute_tool(tool, execution_args)
            self._log_execution_success(tool_id, tool_name, tool_type, result, start_time)
            return result
        except Exception as e:  # noqa: BLE001 - tool failures are logged before propagation
            self._log_execution_failure(tool_id, tool_name, tool_type, e, start_time)
            raise

    def _get_tool_type_name(self, tool: ToolType) -> str:
        """Get the type name for a tool."""
        if isinstance(tool, MethodTool):
            return "METHOD"
        if isinstance(tool, FunctionTool):
            return "FUNCTION"
        if isinstance(tool, ModelTool):
            return "MODEL"
        return "MCP"

    def _execute_tool(self, tool: ToolType, args: dict[str, object]) -> object:
        """Execute a tool and return the result."""
        if isinstance(tool, MethodTool):
            return self._execute_method_tool(tool, args)
        if isinstance(tool, FunctionTool):
            return self._execute_function_tool(tool, args)
        if isinstance(tool, ModelTool):
            # Fail-closed guard: model tools execute via ``ModelExecutionStrategy``
            # (wired into ``ToolExecutionService``'s strategy registry), never
            # through this base isinstance dispatch. Reaching here means a bare
            # ``BasicToolExecutionService`` was handed a model tool — an
            # unsupported configuration. Raise rather than silently drop it.
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=(
                    "model tools execute via ModelExecutionStrategy; "
                    "the base executor received a ModelTool"
                ),
            )
        server = tool.server
        if server is None:
            raise TypeError("McpTool has no MCP server reference")
        return cast(McpServerProtocol, server).call_tool(tool.mcp_tool_name, args)

    def _execute_function_tool(self, tool: FunctionTool, args: dict[str, object]) -> object:
        """Execute a function tool."""
        return tool.func(**self._coerce_args_to_signature(tool.func, args))

    def _execute_method_tool(self, tool: MethodTool, args: dict[str, object]) -> object:
        """Execute a method tool.

        Structurally identical to a function tool: the bound method on
        ``tool.func`` is already a callable that closes over the toolset
        instance, so the executor just invokes it.
        """
        return tool.func(**self._coerce_args_to_signature(tool.func, args))

    def _coerce_args_to_signature(
        self,
        func: CallableObject,
        args: dict[str, object],
    ) -> dict[str, object]:
        """Coerce raw dict args to typed instances based on the function signature.

        The LLM produces tool-call arguments as plain JSON objects (dicts /
        lists / scalars). When a function tool declares a parameter typed
        with a Pydantic model — e.g. ``def optimize_vehicle(sensor_data:
        VehicleState, ...)`` — the function body almost always uses
        attribute access (``sensor_data.speed_kmh``) and will fail with
        ``'dict' object has no attribute 'speed_kmh'`` if we hand it the
        raw dict. Python's runtime doesn't enforce type hints, so we have
        to coerce explicitly here.

        Strategy: walk the callable's signature, and for every parameter
        whose annotation is resolvable, run the value through Pydantic's
        ``TypeAdapter``. That handles:

          * bare Pydantic models (``VehicleState``)
          * containers of Pydantic models (``list[SensorReading]``,
            ``dict[str, EngineMetrics]``)
          * unions / optionals
          * scalar types (no-ops on already-correct values)

        Failures are non-fatal: if ``TypeAdapter`` rejects the value
        (genuinely incompatible shape), we leave the original value in
        place and let the function raise its own clearer error. This
        keeps the coercion opportunistic — calls that already worked
        with raw dicts won't break.
        """
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            return args

        # PEP 563 / ``from __future__ import annotations`` stores annotations
        # as strings; ``typing.get_type_hints`` resolves those forward refs
        # against the callable's module namespace so we get the actual class
        # (and parameterized generics like ``list[VehicleState]``) instead of
        # the string ``"VehicleState"`` that ``TypeAdapter`` can't use.
        try:
            resolved_hints = cast(dict[str, object], get_type_hints(func))
        except Exception:  # noqa: BLE001 - annotation resolution can legitimately fail
            resolved_hints: dict[str, object] = {}

        coerced: dict[str, object] = dict(args)
        for name, value in args.items():
            annotation = resolved_hints.get(name)
            if annotation is None:
                # Fall back to whatever ``inspect.signature`` saw — some
                # callables expose annotations that aren't in
                # ``get_type_hints`` (e.g. wrapped C extensions).
                param = sig.parameters.get(name)
                if param is None:
                    continue
                annotation = cast(object, param.annotation)
                if annotation is inspect.Parameter.empty:
                    continue
                if isinstance(annotation, str):
                    # Unresolved forward ref — skip rather than fail.
                    continue
            try:
                coerced[name] = cast(object, TypeAdapter(annotation).validate_python(value))
            except Exception:  # noqa: BLE001 - opportunistic; preserve original on failure
                continue
        return coerced

    # MARK: - Logging

    def _log_execution_start(
        self, tool_id: str, tool_name: str, tool_type: str, args: dict[str, object]
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
        result: object,
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

    def to_jsonable(self, obj: object) -> JsonValue:
        """Convert a result to a JSON-serializable structure."""
        return _shared_to_jsonable(obj)


def _string_attr(obj: object, attr: str) -> str | None:
    value = cast(object, getattr(obj, attr, None))
    return value if isinstance(value, str) and value else None


__all__ = ["BasicToolExecutionService", "ToolType"]
