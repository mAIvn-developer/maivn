"""Tool event dispatcher package."""

# pyright: strict
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, cast

from maivn_shared import ToolCall
from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

from maivn._internal.core import ToolEventPayload, ToolEventValue
from maivn._internal.utils.logging import get_optional_logger
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from ...execution import BackgroundExecutor
from ..tool_execution_service import ToolExecutionService
from .execution import elapsed_ms, post_tool_result, run_tool
from .logging_helpers import log_tool_complete, log_tool_start
from .reporting import (
    report_tool_complete,
    report_tool_error,
    report_tool_start,
    sanitize_args_for_reporting,
    summarize_injected_keys,
)

# MARK: Types

JsonObject = dict[str, JsonValue]
JsonArray = list[JsonValue]


class ToolEventCoordinator(Protocol):
    """Coordinator surface used by the dispatcher."""

    _scope: object | None

    def execute_tool_events(self, tool_events: dict[str, ToolEventPayload]) -> JsonObject:
        """Execute pending tool events."""
        ...

    def execute_tool_batch(
        self,
        tools: list[JsonObject],
        *,
        on_tool_complete: Callable[[int, str, object], None],
    ) -> JsonArray:
        """Execute a batch of tool calls."""
        ...

    def get_tool_results(self) -> dict[str, object]:
        """Return prior tool results."""
        ...

    def _store_result(self, tool_id: str, tool: object | None, result: object) -> None:
        """Store one tool execution result."""
        ...


# MARK: Dispatcher


class ToolEventDispatcher:
    """Dispatch tool events through execution services and background workers."""

    def __init__(
        self,
        *,
        coordinator: ToolEventCoordinator,
        tool_execution_service: ToolExecutionService,
        background_executor: BackgroundExecutor,
        post_resume: Callable[[str, JsonObject], None],
        reporter_supplier: Callable[[], BaseReporter | None],
        progress_task_supplier: Callable[[], object | None],
        agent_count_supplier: Callable[[], int],
        tool_agent_lookup: Callable[[str], str | None],
        swarm_name_supplier: Callable[[], str | None] | None = None,
        logger: LoggerProtocol | None = None,
    ) -> None:
        self._coordinator: ToolEventCoordinator = coordinator
        self._tool_execution_service: ToolExecutionService = tool_execution_service
        self._background_executor: BackgroundExecutor = background_executor
        self._post_resume: Callable[[str, JsonObject], None] = post_resume
        self._get_reporter: Callable[[], BaseReporter | None] = reporter_supplier
        self._get_progress_task: Callable[[], object | None] = progress_task_supplier
        self._get_agent_count: Callable[[], int] = agent_count_supplier
        self._tool_agent_lookup: Callable[[str], str | None] = tool_agent_lookup
        self._get_swarm_name: Callable[[], str | None] = swarm_name_supplier or (lambda: None)
        self._logger: LoggerProtocol = logger or get_optional_logger()

    # MARK: - Helper Surfaces

    @property
    def coordinator(self) -> ToolEventCoordinator:
        return self._coordinator

    @property
    def tool_execution_service(self) -> ToolExecutionService:
        return self._tool_execution_service

    @property
    def logger(self) -> LoggerProtocol:
        return self._logger

    def post_resume(self, resume_url: str, payload: JsonObject) -> None:
        self._post_resume(resume_url, payload)

    def get_tool_name(self, tool_id: str) -> str:
        return self._get_tool_name(tool_id)

    def get_tool_agent_name(self, tool_id: str) -> str | None:
        return self._tool_agent_lookup(tool_id)

    def get_swarm_name(self) -> str | None:
        return self._get_swarm_name()

    @staticmethod
    def summarize_injected_keys(payload: JsonValue) -> list[str]:
        return summarize_injected_keys(payload)

    def _get_tool_name(self, tool_id: str) -> str:
        """Look up the tool name from tool_id. Falls back to tool_id."""
        try:
            tool = self._tool_execution_service.resolve_tool(tool_id)
            return getattr(tool, "name", tool_id)
        except (ValueError, KeyError):
            return tool_id

    def submit_tool_call(
        self,
        tool_event_id: str,
        tool_call_payload: JsonObject,
        resume_url: str,
    ) -> None:
        """Execute a single tool call asynchronously and post the result."""
        _ = self._background_executor.submit(
            lambda: self._execute_tool_call(tool_event_id, tool_call_payload, resume_url)
        )

    def process_tool_requests(
        self,
        tool_events: dict[str, ToolEventPayload],
        resume_url: str,
    ) -> None:
        """Execute accumulated tool requests and post back results."""
        if not tool_events:
            return

        resume_payload = self._coordinator.execute_tool_events(tool_events)
        if resume_payload:
            for event_id, result in resume_payload.items():
                self._post_resume(resume_url, {"tool_event_id": event_id, "result": result})

    def process_tool_batch(
        self,
        tool_event_id: str,
        value: ToolEventValue,
        resume_url: str,
    ) -> None:
        """Handle batched tool execution events."""
        tools = _tool_calls_from_value(value.get("tool_calls", []))
        if not tools:
            self._post_resume(
                resume_url,
                {"tool_event_id": tool_event_id, "result": {"results": []}},
            )
            self._logger.warning("Batch tool event had no tool_calls; resumed empty")
            return

        reporter = self._get_reporter()
        progress_task = self._get_progress_task()

        for tool_call in tools:
            batch_tool_id = _string_value(tool_call.get("tool_id"), "")
            if batch_tool_id and reporter:
                self._report_tool_start(
                    batch_tool_id,
                    batch_tool_id,
                    reporter,
                    progress_task,
                    _json_object_value(tool_call.get("args")),
                )

        def _on_complete(_idx: int, tool_id: str, result: object) -> None:
            if reporter and tool_id:
                self._report_tool_complete(tool_id, 0, result, reporter)

        results_ordered = self._coordinator.execute_tool_batch(
            tools,
            on_tool_complete=_on_complete,
        )
        self._post_resume(
            resume_url,
            {
                "tool_event_id": tool_event_id,
                "result": {"results": results_ordered},
            },
        )

    def acknowledge_barrier(self, tool_event_id: str, resume_url: str) -> None:
        """Send barrier acknowledgement back to the server."""
        self._post_resume(resume_url, {"tool_event_id": tool_event_id, "result": "ok"})

    def _execute_tool_call(
        self,
        tool_event_id: str,
        tool_call_payload: JsonObject,
        resume_url: str,
    ) -> None:
        """Execute a tool call and post the result."""
        tool_id = _string_value(tool_call_payload.get("tool_id"), "")
        args = _json_object_value(tool_call_payload.get("args"))
        private_data_injected = tool_call_payload.get("private_data_injected")
        interrupt_data_injected = tool_call_payload.get("interrupt_data_injected")

        self._log_tool_start(
            tool_id,
            tool_event_id,
            args,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

        reporter = self._get_reporter()
        self._logger.info(
            "[DISPATCHER] _execute_tool_call: reporter=%s, type=%s",
            reporter,
            type(reporter).__name__ if reporter else None,
        )
        progress_task = self._get_progress_task()
        self._report_tool_start(
            tool_id,
            tool_event_id,
            reporter,
            progress_task,
            args,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

        start = time.perf_counter()
        try:
            value = self._run_tool(
                tool_id,
                args,
                private_data_injected,
                interrupt_data_injected,
                tool_event_id=tool_event_id,
            )
            elapsed_ms_value = self._elapsed_ms(start)
            self._report_tool_complete(
                tool_event_id,
                elapsed_ms_value,
                value,
                reporter,
                private_data_injected=private_data_injected,
                interrupt_data_injected=interrupt_data_injected,
            )
        except Exception as exc:  # noqa: BLE001 - async tool-call errors post as tool results
            value = f"error:{exc}"
            self._logger.exception(f"Async tool execution failed for {tool_id}: {exc}")
            self._report_tool_error(tool_id, str(exc), tool_event_id, reporter)
        finally:
            elapsed_ms_value = self._elapsed_ms(start)
            self._log_tool_complete(tool_id, tool_event_id, elapsed_ms_value)

        self._post_tool_result(tool_event_id, value, resume_url)

    def _run_tool(
        self,
        tool_id: str,
        args: JsonObject,
        private_data_injected: JsonValue,
        interrupt_data_injected: JsonValue,
        *,
        tool_event_id: str | None = None,
    ) -> JsonValue:
        return run_tool(
            self,
            tool_id,
            args,
            private_data_injected,
            interrupt_data_injected,
            tool_event_id=tool_event_id,
        )

    def _post_tool_result(self, tool_event_id: str, value: JsonValue, resume_url: str) -> None:
        post_tool_result(self, tool_event_id, value, resume_url)

    def _log_tool_start(
        self,
        tool_id: str,
        tool_event_id: str,
        args: JsonObject,
        *,
        private_data_injected: JsonValue,
        interrupt_data_injected: JsonValue,
    ) -> None:
        log_tool_start(
            self,
            tool_id,
            tool_event_id,
            args,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

    def _log_tool_complete(self, tool_id: str, tool_event_id: str, elapsed_ms_value: int) -> None:
        log_tool_complete(self, tool_id, tool_event_id, elapsed_ms_value)

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return elapsed_ms(start)

    def _report_tool_start(
        self,
        tool_id: str,
        tool_event_id: str,
        reporter: BaseReporter | None,
        progress_task: object | None,
        tool_args: JsonObject | None,
        *,
        private_data_injected: JsonValue = None,
        interrupt_data_injected: JsonValue = None,
    ) -> None:
        report_tool_start(
            self,
            tool_id,
            tool_event_id,
            reporter,
            progress_task,
            tool_args,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

    @staticmethod
    def _summarize_injected_keys(payload: JsonValue) -> list[str]:
        return summarize_injected_keys(payload)

    @staticmethod
    def _sanitize_args_for_reporting(
        args: JsonObject | None,
        *,
        private_data_injected: JsonValue,
        interrupt_data_injected: JsonValue,
    ) -> dict[str, object] | None:
        return sanitize_args_for_reporting(
            args,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

    def _report_tool_complete(
        self,
        tool_event_id: str,
        elapsed_ms_value: int,
        result: object,
        reporter: BaseReporter | None,
        *,
        private_data_injected: JsonValue = None,
        interrupt_data_injected: JsonValue = None,
    ) -> None:
        report_tool_complete(
            tool_event_id,
            elapsed_ms_value,
            result,
            reporter,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

    def _report_tool_error(
        self,
        tool_id: str,
        error_message: str,
        tool_event_id: str,
        reporter: BaseReporter | None,
    ) -> None:
        report_tool_error(tool_id, error_message, tool_event_id, reporter)


# MARK: Helpers


def _tool_calls_from_value(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []

    tool_calls: list[JsonObject] = []
    for item in cast(list[object], value):
        if isinstance(item, ToolCall):
            tool_calls.append({"tool_id": item.tool_id, "args": item.args})
        elif isinstance(item, dict):
            tool_calls.append(_json_object_from_mapping(cast(dict[object, object], item)))
    return tool_calls


def _json_object_from_mapping(value: dict[object, object]) -> JsonObject:
    return {str(key): cast(JsonValue, item) for key, item in value.items()}


def _json_object_value(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return _json_object_from_mapping(cast(dict[object, object], value))


def _string_value(value: JsonValue, fallback: str) -> str:
    return value if isinstance(value, str) else fallback


__all__ = ["ToolEventDispatcher"]
