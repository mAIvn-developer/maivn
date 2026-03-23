"""Tool event dispatcher package."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

from maivn_shared.infrastructure.logging import LoggerProtocol

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


class ToolEventDispatcher:
    """Dispatch tool events through execution services and background workers."""

    def __init__(
        self,
        *,
        coordinator: Any,
        tool_execution_service: ToolExecutionService,
        background_executor: BackgroundExecutor,
        post_resume: Callable[[str, dict[str, Any]], None],
        reporter_supplier: Callable[[], BaseReporter | None],
        progress_task_supplier: Callable[[], Any | None],
        agent_count_supplier: Callable[[], int],
        tool_agent_lookup: Callable[[str], str | None],
        swarm_name_supplier: Callable[[], str | None] | None = None,
        logger: LoggerProtocol | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._tool_execution_service = tool_execution_service
        self._background_executor = background_executor
        self._post_resume = post_resume
        self._get_reporter = reporter_supplier
        self._get_progress_task = progress_task_supplier
        self._get_agent_count = agent_count_supplier
        self._tool_agent_lookup = tool_agent_lookup
        self._get_swarm_name = swarm_name_supplier or (lambda: None)
        self._logger: LoggerProtocol = logger or get_optional_logger()

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
        tool_call_payload: dict[str, Any],
        resume_url: str,
    ) -> None:
        """Execute a single tool call asynchronously and post the result."""
        self._background_executor.submit(
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
        tools = value.get("tool_calls", []) or []
        if not tools:
            self._post_resume(
                resume_url,
                {"tool_event_id": tool_event_id, "result": {"results": []}},
            )
            self._logger.warning("Batch tool event had no tool_calls; resumed empty")
            return

        tools_dict = cast(list[dict[str, Any]], tools)
        reporter = self._get_reporter()
        progress_task = self._get_progress_task()

        for tool_call in tools_dict:
            batch_tool_id = str(tool_call.get("tool_id", ""))
            if batch_tool_id and reporter:
                self._report_tool_start(
                    batch_tool_id,
                    batch_tool_id,
                    reporter,
                    progress_task,
                    tool_call.get("args"),
                )

        def _on_complete(_idx: int, tool_id: str, result: Any) -> None:
            if reporter and tool_id:
                self._report_tool_complete(tool_id, 0, result, reporter)

        results_ordered = self._coordinator.execute_tool_batch(
            tools_dict,
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
        tool_call_payload: dict[str, Any],
        resume_url: str,
    ) -> None:
        """Execute a tool call and post the result."""
        tool_id = str(tool_call_payload.get("tool_id", ""))
        args = tool_call_payload.get("args", {}) or {}
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
        except Exception as exc:  # noqa: BLE001
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
        args: dict[str, Any],
        private_data_injected: Any,
        interrupt_data_injected: Any,
    ) -> Any:
        return run_tool(
            self,
            tool_id,
            args,
            private_data_injected,
            interrupt_data_injected,
        )

    def _post_tool_result(self, tool_event_id: str, value: Any, resume_url: str) -> None:
        post_tool_result(self, tool_event_id, value, resume_url)

    def _log_tool_start(
        self,
        tool_id: str,
        tool_event_id: str,
        args: dict[str, Any],
        *,
        private_data_injected: Any,
        interrupt_data_injected: Any,
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
        progress_task: Any | None,
        tool_args: dict[str, Any] | None,
        *,
        private_data_injected: Any = None,
        interrupt_data_injected: Any = None,
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
    def _summarize_injected_keys(payload: Any) -> list[str]:
        return summarize_injected_keys(payload)

    @staticmethod
    def _sanitize_args_for_reporting(
        args: dict[str, Any] | None,
        *,
        private_data_injected: Any,
        interrupt_data_injected: Any,
    ) -> dict[str, Any] | None:
        return sanitize_args_for_reporting(
            args,
            private_data_injected=private_data_injected,
            interrupt_data_injected=interrupt_data_injected,
        )

    def _report_tool_complete(
        self,
        tool_event_id: str,
        elapsed_ms_value: int,
        result: Any,
        reporter: BaseReporter | None,
        *,
        private_data_injected: Any = None,
        interrupt_data_injected: Any = None,
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


__all__ = ["ToolEventDispatcher"]
