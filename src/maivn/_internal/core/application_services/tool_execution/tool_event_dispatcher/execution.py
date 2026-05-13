"""Execution helpers for ToolEventDispatcher."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from maivn._internal.core.entities.execution_context import ExecutionContext

if TYPE_CHECKING:
    from .dispatcher import ToolEventDispatcher


# MARK: Tool Execution


def run_tool(
    dispatcher: ToolEventDispatcher,
    tool_id: str,
    args: dict[str, Any],
    private_data_injected: Any,
    interrupt_data_injected: Any,
    *,
    tool_event_id: str | None = None,
) -> Any:
    """Execute the tool and return the serialized result.

    ``tool_event_id`` is forwarded so per-tool hook firings can be routed
    to the correct tool card by the frontend.
    """
    metadata: dict[str, Any] | None = None
    if private_data_injected or interrupt_data_injected:
        metadata = {
            "private_data_injected": private_data_injected,
            "interrupt_data_injected": interrupt_data_injected,
        }

    scope = getattr(dispatcher._coordinator, "_scope", None)
    context_overrides = ExecutionContext(
        scope=scope,
        tool_results=dispatcher._coordinator.get_tool_results(),
        metadata=metadata,
    )
    result = dispatcher._tool_execution_service.execute_tool_call(
        tool_id,
        args,
        context=context_overrides,
        tool_event_id=tool_event_id,
    )
    value = dispatcher._tool_execution_service.to_jsonable(result)

    try:
        tool = dispatcher._tool_execution_service.resolve_tool(tool_id)
    except Exception:
        tool = None
    dispatcher._coordinator._store_result(tool_id, tool, result)

    return value


def post_tool_result(
    dispatcher: ToolEventDispatcher,
    tool_event_id: str,
    value: Any,
    resume_url: str,
) -> None:
    """Post the tool execution result to the resume URL."""
    try:
        dispatcher._post_resume(
            resume_url,
            {"tool_event_id": tool_event_id, "result": value},
        )
    except Exception as exc:  # noqa: BLE001
        dispatcher._logger.error("Failed posting async result for %s: %s", tool_event_id, exc)


# MARK: Timing


def elapsed_ms(start: float) -> int:
    """Calculate elapsed milliseconds since start time."""
    return int((time.perf_counter() - start) * 1000.0)
