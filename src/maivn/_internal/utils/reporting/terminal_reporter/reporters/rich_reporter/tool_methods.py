"""Tool and system-tool methods for ``RichReporter``."""

from __future__ import annotations

from typing import Any

# MARK: Tool Methods


class RichReporterToolMixin:
    enabled: bool
    _terminal_lock: Any
    _progress_manager: Any
    _tool_reporter: Any
    _active_system_tool_event_ids: set[str]
    _system_tool_stream_group_event_ids: set[str]
    _handle_system_tool_start: Any
    _handle_system_tool_complete: Any
    _finalize_system_tool_event: Any

    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
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

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
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

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: Any | None = None,
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


class RichReporterSystemToolMixin:
    _active_system_tool_event_ids: set[str]
    _system_tool_stream_group_event_ids: set[str]
    _tool_reporter: Any
    _progress_manager: Any

    def _handle_system_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None,
        agent_name: str | None,
        tool_args: dict[str, Any] | None,
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
        result: Any | None,
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
