"""Execution helpers for ToolEventDispatcher."""

# pyright: strict
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, cast

from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

from maivn._internal.core.entities.execution_context import ExecutionContext

from ..tool_execution_service import ToolExecutionService

JsonObject = dict[str, JsonValue]


# MARK: Protocols


class ToolEventCoordinatorRuntime(Protocol):
    """Coordinator surface consumed by execution helpers."""

    def get_tool_results(self) -> dict[str, object]:
        """Return prior tool results."""
        ...


class ToolEventDispatcherRuntime(Protocol):
    """Dispatcher surface consumed by execution helpers."""

    @property
    def coordinator(self) -> ToolEventCoordinatorRuntime:
        """Coordinator for tool event execution."""
        ...

    @property
    def tool_execution_service(self) -> ToolExecutionService:
        """Tool execution service used to run tool calls."""
        ...

    def post_resume(self, resume_url: str, payload: JsonObject) -> None:
        """Post a resume payload."""
        ...

    @property
    def logger(self) -> LoggerProtocol:
        """Logger used for helper diagnostics."""
        ...


# MARK: Tool Execution


def run_tool(
    dispatcher: ToolEventDispatcherRuntime,
    tool_id: str,
    args: dict[str, JsonValue],
    private_data_injected: JsonValue,
    interrupt_data_injected: JsonValue,
    *,
    tool_event_id: str | None = None,
) -> JsonValue:
    """Execute the tool and return the serialized result.

    ``tool_event_id`` is forwarded so per-tool hook firings can be routed
    to the correct tool card by the frontend.
    """
    metadata: dict[str, object] | None = None
    if private_data_injected or interrupt_data_injected:
        metadata = {
            "private_data_injected": private_data_injected,
            "interrupt_data_injected": interrupt_data_injected,
        }

    scope = _coordinator_scope(dispatcher.coordinator)
    context_overrides = ExecutionContext(
        scope=scope,
        tool_results=dispatcher.coordinator.get_tool_results(),
        metadata=metadata,
    )
    result = dispatcher.tool_execution_service.execute_tool_call(
        tool_id,
        args,
        context=context_overrides,
        tool_event_id=tool_event_id,
    )
    value = dispatcher.tool_execution_service.to_jsonable(result)

    try:
        tool = dispatcher.tool_execution_service.resolve_tool(tool_id)
    except Exception:  # noqa: BLE001 - result storage should proceed without tool metadata
        tool = None
    _store_result(dispatcher.coordinator, tool_id, tool, result)

    return value


def post_tool_result(
    dispatcher: ToolEventDispatcherRuntime,
    tool_event_id: str,
    value: JsonValue,
    resume_url: str,
) -> None:
    """Post the tool execution result to the resume URL."""
    try:
        dispatcher.post_resume(
            resume_url,
            {"tool_event_id": tool_event_id, "result": value},
        )
    except Exception as exc:  # noqa: BLE001 - posting errors are logged without raising background tasks
        dispatcher.logger.error("Failed posting async result for %s: %s", tool_event_id, exc)


# MARK: Timing


def elapsed_ms(start: float) -> int:
    """Calculate elapsed milliseconds since start time."""
    return int((time.perf_counter() - start) * 1000.0)


def _coordinator_scope(coordinator: object) -> object | None:
    return cast(object | None, getattr(coordinator, "_scope", None))


def _store_result(
    coordinator: object,
    tool_id: str,
    tool: object | None,
    result: object,
) -> None:
    store = cast(object, getattr(coordinator, "_store_result", None))
    if callable(store):
        cast(Callable[[str, object | None, object], None], store)(tool_id, tool, result)
