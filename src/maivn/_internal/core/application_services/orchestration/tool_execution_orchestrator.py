"""Tool execution orchestrator for handling all tool execution modes.

This module extracts tool execution logic from AgentOrchestrator to follow
Single Responsibility Principle.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import as_completed
from typing import Any, cast

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core import ToolEventPayload, ToolEventValue
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.utils.logging import get_optional_logger

from ..execution import BackgroundExecutor
from ..helpers import get_optimal_worker_count
from ..tool_execution.tool_execution_service import ToolExecutionService


class ToolExecutionOrchestrator:
    """Orchestrates tool execution in various modes (parallel, sequential, batch).

    Thread-safe: Uses a lock to protect concurrent access to tool results.
    """

    # MARK: - Initialization

    def __init__(
        self,
        tool_execution_service: ToolExecutionService,
        *,
        logger: LoggerProtocol | None = None,
        scope: Any | None = None,
        default_timeout: float | None = None,
        enable_background_execution: bool = True,
    ) -> None:
        """Initialize tool execution orchestrator.

        Args:
            tool_execution_service: Service for executing individual tools
            logger: Optional logger instance
            scope: Optional execution scope
            default_timeout: Optional default timeout for tool execution
        """
        self._tool_execution = tool_execution_service
        self._logger: LoggerProtocol = logger or get_optional_logger()

        self._tool_results: dict[str, Any] = {}
        self._results_lock = threading.Lock()

        self._scope = scope
        self._default_timeout = default_timeout
        self._last_messages: list[Any] | None = None
        self._enable_background_execution = enable_background_execution

    # MARK: - Public API

    def execute_tool_events(
        self,
        tool_events: Mapping[str, ToolEventPayload],
    ) -> dict[str, Any]:
        """Execute tool events, automatically choosing parallel or sequential mode.

        Args:
            tool_events: Dictionary of tool event IDs to payloads

        Returns:
            Dictionary mapping event IDs to results
        """
        if not tool_events:
            return {}

        if not self._enable_background_execution:
            return self._execute_sequential(tool_events)

        independent_tools = self._find_independent_tool_events(tool_events)

        if len(independent_tools) > 1:
            return self._execute_parallel(independent_tools)

        return self._execute_sequential(tool_events)

    def execute_tool_batch(
        self,
        tool_calls: Sequence[Mapping[str, Any]],
        on_tool_complete: Callable[[int, str, Any], None] | None = None,
    ) -> list[Any]:
        """Execute a batch of tool calls in parallel.

        Args:
            tool_calls: List of tool call dictionaries
            on_tool_complete: Optional callback ``(index, tool_id, result)``
                invoked as each individual tool finishes, enabling real-time
                per-tool completion reporting.

        Returns:
            List of results in the same order as input
        """
        if not tool_calls:
            return []

        calls_list = list(tool_calls)
        if not self._enable_background_execution:
            results: list[Any] = []
            for idx, call in enumerate(calls_list):
                _, result_value = self._execute_indexed(call, idx)
                results.append(result_value)
                if on_tool_complete is not None:
                    tool_id = str(call.get("tool_id", ""))
                    on_tool_complete(idx, tool_id, result_value)
            return results

        results_ordered: list[Any] = [""] * len(calls_list)
        max_workers = max(1, min(len(calls_list), get_optimal_worker_count()))

        with BackgroundExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._execute_indexed, call, idx): idx
                for idx, call in enumerate(calls_list)
            }
            for future in as_completed(futures):
                idx, result_value = future.result()
                results_ordered[idx] = result_value
                if on_tool_complete is not None:
                    tool_id = str(calls_list[idx].get("tool_id", ""))
                    on_tool_complete(idx, tool_id, result_value)

        return results_ordered

    def get_tool_results(self) -> dict[str, Any]:
        """Get raw tool results for dependency resolution.

        Returns:
            Copy of the dictionary of tool IDs to raw results
        """
        with self._results_lock:
            return dict(self._tool_results)

    def update_messages(self, messages: Sequence[Any]) -> None:
        """Store latest message list for dependency context propagation."""
        self._last_messages = list(messages)

    def clear_results(self) -> None:
        """Clear cached results."""
        with self._results_lock:
            self._tool_results.clear()

    # MARK: - Context Building

    def build_context(
        self, overrides: ExecutionContext | dict[str, Any] | None = None
    ) -> ExecutionContext:
        """Create an execution context merged with optional overrides."""
        tool_results_snapshot = self.get_tool_results()

        if isinstance(overrides, ExecutionContext):
            scope = overrides.scope if overrides.scope is not None else self._scope
            tool_results = (
                overrides.tool_results
                if overrides.tool_results is not None
                else tool_results_snapshot
            )
            messages = (
                overrides.messages if overrides.messages is not None else self._get_messages_copy()
            )
            timeout = (
                overrides.timeout
                if overrides.timeout is not None
                else self._resolve_timeout(scope, None)
            )
            return overrides.copy_with(
                scope=scope,
                tool_results=tool_results,
                messages=messages,
                timeout=timeout,
            )

        overrides = overrides or {}
        scope = overrides.get("scope", self._scope)

        return ExecutionContext(
            scope=scope,
            tool_results=overrides.get("tool_results", tool_results_snapshot),
            messages=overrides.get("messages") or self._get_messages_copy(),
            timeout=self._resolve_timeout(scope, overrides.get("timeout")),
            metadata=overrides.get("metadata"),
        )

    def _get_messages_copy(self) -> list[Any] | None:
        """Get a copy of the last messages if available."""
        return list(self._last_messages) if self._last_messages else None

    def _resolve_timeout(self, scope: Any | None, override: Any | None) -> float | None:
        """Resolve timeout from override, scope, or default."""
        if override is not None:
            return override
        if scope and getattr(scope, "timeout", None) is not None:
            return scope.timeout
        return self._default_timeout

    # MARK: - Tool Discovery

    def _find_independent_tool_events(
        self, tool_events: Mapping[str, ToolEventPayload]
    ) -> dict[str, ToolEventPayload]:
        """Identify tool events that can be executed in parallel.

        Args:
            tool_events: Dictionary of tool event IDs to payloads

        Returns:
            Dictionary of independent tool events
        """
        return {
            event_id: payload
            for event_id, payload in tool_events.items()
            if self._is_tool_call_event(payload)
        }

    def _is_tool_call_event(self, payload: ToolEventPayload) -> bool:
        """Check if payload represents a tool call event."""
        value = cast(ToolEventValue, payload.get("value", {}))
        return isinstance(value, dict) and bool(value.get("tool_call"))

    # MARK: - Parallel Execution

    def _execute_parallel(self, tool_events: Mapping[str, ToolEventPayload]) -> dict[str, Any]:
        """Execute multiple independent tools in parallel.

        Args:
            tool_events: Dictionary of tool event IDs to payloads

        Returns:
            Dictionary mapping event IDs to results
        """
        max_workers = min(len(tool_events), get_optimal_worker_count())
        results: dict[str, Any] = {}

        with BackgroundExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._execute_event, evt_id, payload): evt_id
                for evt_id, payload in tool_events.items()
            }
            for future in as_completed(futures):
                event_id, result = future.result()
                results[event_id] = result

        return results

    # MARK: - Sequential Execution

    def _execute_sequential(self, tool_events: Mapping[str, ToolEventPayload]) -> dict[str, Any]:
        """Execute tools sequentially (for tools with dependencies).

        Args:
            tool_events: Dictionary of tool event IDs to payloads

        Returns:
            Dictionary mapping event IDs to results
        """
        results: dict[str, Any] = {}

        for event_id, payload in tool_events.items():
            results[event_id] = self._process_tool_event(payload)

        return results

    def _process_tool_event(self, payload: ToolEventPayload) -> Any:
        """Process a single tool event payload.

        Args:
            payload: Tool event payload

        Returns:
            Execution result or error string
        """
        value = cast(ToolEventValue, payload.get("value", {}))

        if not isinstance(value, dict):
            return "error:invalid_payload"

        if not value.get("tool_call"):
            return "error:unknown_tool_event"

        tool_call = self._normalize_tool_call(value.get("tool_call"))
        tool_id = str(tool_call.get("tool_id", ""))
        args = tool_call.get("args", {}) or {}

        try:
            _, serialized = self._execute_tool(tool_id, args)
            return serialized
        except Exception as exc:
            self._logger.exception(f"Tool execution failed for {tool_id}: {exc}")
            return f"error:{exc}"

    # MARK: - Single Tool Execution

    def _execute_event(self, event_id: str, payload: ToolEventPayload) -> tuple[str, Any]:
        """Execute a single tool event.

        Args:
            event_id: Tool event ID
            payload: Tool event payload

        Returns:
            Tuple of (event_id, result)
        """
        value = cast(ToolEventValue, payload.get("value", {}))
        tool_call = self._normalize_tool_call(value.get("tool_call"))
        tool_id = str(tool_call.get("tool_id", ""))
        args = tool_call.get("args", {}) or {}

        try:
            _, serialized = self._execute_tool(tool_id, args)
            return event_id, serialized
        except Exception as exc:
            self._logger.exception(f"Parallel tool execution failed for {tool_id}: {exc}")
            return event_id, f"error:{exc}"

    def _execute_indexed(self, call: Mapping[str, Any], idx: int) -> tuple[int, Any]:
        """Execute a single tool call with index (for batch execution).

        Args:
            call: Tool call dictionary
            idx: Index in batch

        Returns:
            Tuple of (index, result)
        """
        tool_id = str(call.get("tool_id", ""))
        args = call.get("args", {}) or {}
        _, serialized = self._execute_tool(tool_id, args)
        return idx, serialized

    def _execute_tool(self, tool_id: str, args: dict[str, Any]) -> tuple[Any, Any]:
        """Execute a single tool and return both raw and serialized results.

        Args:
            tool_id: Tool identifier
            args: Tool arguments

        Returns:
            Tuple of (raw_result, serialized_result)
        """
        context = self.build_context()
        tool = self._resolve_tool_safely(tool_id)
        result = self._tool_execution.execute_tool_call(tool_id, args, context)
        serialized = self._tool_execution.to_jsonable(result)

        self._store_result(tool_id, tool, result)

        return result, serialized

    def _resolve_tool_safely(self, tool_id: str) -> Any | None:
        """Safely resolve a tool, returning None on failure."""
        try:
            return self._tool_execution.resolve_tool(tool_id)
        except Exception as e:
            self._logger.debug("[TOOL_EXEC] Failed to resolve tool '%s': %s", tool_id, e)
            return None

    def _store_result(self, tool_id: str, tool: Any | None, result: Any) -> None:
        """Store tool result for dependency resolution (thread-safe)."""
        with self._results_lock:
            self._tool_results[tool_id] = result

            if tool is None:
                return

            tool_type = getattr(tool, "tool_type", None)
            if tool_type != "agent":
                return

            agent_id = getattr(tool, "target_agent_id", None) or getattr(tool, "agent_id", None)
            if agent_id:
                self._tool_results[str(agent_id)] = result

    # MARK: - Payload Normalization

    def _normalize_tool_call(self, payload: Any) -> dict[str, Any]:
        """Normalize tool call payload into a dictionary."""
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "model_dump"):
            return cast(dict[str, Any], payload.model_dump(mode="json"))
        return {}


__all__ = [
    "ToolExecutionOrchestrator",
]
