"""Tool and system-tool methods for ``RichReporter``."""

# pyright: strict
from __future__ import annotations

from abc import ABC
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from typing_extensions import override

from ...base.defaults import ReporterDefaultEventsMixin
from ...base.interface import BaseReporterInterface
from .progress import ProgressManager
from .reporting import ToolReporter

ToolArgs = dict[str, object]


# MARK: Tool Methods


class RichReporterToolMixin(ReporterDefaultEventsMixin, BaseReporterInterface, ABC):
    if TYPE_CHECKING:
        enabled: bool
        _terminal_lock: AbstractContextManager[bool]
        _progress_manager: ProgressManager
        _tool_reporter: ToolReporter
        _active_system_tool_event_ids: set[str]
        _system_tool_stream_group_event_ids: set[str]

        def _handle_system_tool_start(
            self,
            tool_name: str,
            event_id: str,
            tool_type: str | None,
            agent_name: str | None,
            tool_args: ToolArgs | None,
        ) -> None:
            _ = (tool_name, event_id, tool_type, agent_name, tool_args)

        def _handle_system_tool_complete(
            self,
            event_id: str,
            elapsed_ms: int | None,
            result: object | None,
        ) -> None:
            _ = (event_id, elapsed_ms, result)

        def _finalize_system_tool_event(self, event_id: str) -> None:
            _ = event_id

    @override
    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: ToolArgs | None = None,
        swarm_name: str | None = None,
    ) -> None:
        """Report tool execution start."""
        _ = swarm_name
        if not self.enabled:
            return

        with self._terminal_lock:
            normalized_type = str(tool_type or "").strip().lower()
            if normalized_type == "system":
                self._handle_system_tool_start(
                    tool_name,
                    event_id,
                    tool_type,
                    agent_name,
                    tool_args,
                )
                return

            with self._progress_manager.prepare_for_user_input():
                self._tool_reporter.report_tool_start(
                    tool_name,
                    event_id,
                    tool_type,
                    agent_name,
                    tool_args,
                )

    @override
    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: object | None = None,
    ) -> None:
        """Report tool execution completion."""
        if not self.enabled:
            return

        with self._terminal_lock:
            if event_id in self._active_system_tool_event_ids:
                self._handle_system_tool_complete(event_id, elapsed_ms, result)
                return

            with self._progress_manager.prepare_for_user_input():
                self._tool_reporter.report_tool_complete(event_id, elapsed_ms, result)

    @override
    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Report tool execution error."""
        if not self.enabled:
            return

        with self._terminal_lock:
            if event_id is not None and event_id in self._active_system_tool_event_ids:
                self._tool_reporter.report_tool_error(
                    tool_name,
                    error,
                    event_id=event_id,
                    elapsed_ms=elapsed_ms,
                )
                self._finalize_system_tool_event(event_id)
                return

            with self._progress_manager.prepare_for_user_input():
                self._tool_reporter.report_tool_error(
                    tool_name,
                    error,
                    event_id=event_id,
                    elapsed_ms=elapsed_ms,
                )

    @override
    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: object | None = None,
    ) -> None:
        """Report MODEL tool execution completion."""
        if not self.enabled:
            return

        with self._terminal_lock:
            with self._progress_manager.prepare_for_user_input():
                self._tool_reporter.report_model_tool_complete(
                    tool_name,
                    event_id=event_id,
                    agent_name=agent_name,
                    swarm_name=swarm_name,
                    result=result,
                )

    @override
    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        """Report system tool execution progress."""
        if not self.enabled:
            return

        with self._terminal_lock:
            if event_id in self._active_system_tool_event_ids:
                self._tool_reporter.report_system_tool_progress(
                    event_id,
                    tool_name,
                    chunk_count,
                    elapsed_seconds,
                    text,
                    stream_group_event_ids=set(self._system_tool_stream_group_event_ids),
                )
                return

            with self._progress_manager.prepare_for_user_input():
                self._tool_reporter.report_system_tool_progress(
                    event_id,
                    tool_name,
                    chunk_count,
                    elapsed_seconds,
                    text,
                )


# MARK: System Tool Helpers


class RichReporterSystemToolMixin(BaseReporterInterface, ABC):
    if TYPE_CHECKING:
        _active_system_tool_event_ids: set[str]
        _system_tool_stream_group_event_ids: set[str]
        _tool_reporter: ToolReporter
        _progress_manager: ProgressManager

    def _handle_system_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None,
        agent_name: str | None,
        tool_args: ToolArgs | None,
    ) -> None:
        """Handle system tool start with Live suspension."""
        should_suspend = not self._active_system_tool_event_ids
        if should_suspend:
            self._system_tool_stream_group_event_ids = set()
            self._tool_reporter.clear_streaming_state()
        self._active_system_tool_event_ids.add(str(event_id))
        self._system_tool_stream_group_event_ids.add(str(event_id))
        if should_suspend:
            self._progress_manager.suspend_live()
        self._tool_reporter.report_tool_start(
            tool_name,
            event_id,
            tool_type,
            agent_name,
            tool_args,
        )

    def _handle_system_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None,
        result: object | None,
    ) -> None:
        """Handle system tool completion with Live resumption."""
        self._tool_reporter.report_tool_complete(event_id, elapsed_ms, result)
        self._finalize_system_tool_event(event_id)

    def _finalize_system_tool_event(self, event_id: str) -> None:
        """Remove system tool event and resume Live if no more active."""
        self._active_system_tool_event_ids.discard(event_id)
        if not self._active_system_tool_event_ids:
            self._tool_reporter.clear_streaming_state()
            self._system_tool_stream_group_event_ids = set()
            self._progress_manager.resume_live()
