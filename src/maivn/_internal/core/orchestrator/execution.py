"""Execution flow for AgentOrchestrator (invoke and stream)."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, cast

from maivn_shared import SessionResponse
from pydantic import ValidationError

from maivn._internal.core import SessionEndpoints, SSEEvent
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from .helpers import (
    extract_latest_response,
    sanitize_user_facing_error_message,
)

if TYPE_CHECKING:
    from .core import AgentOrchestrator


# MARK: Invoke Execution


def execute_invoke(
    orch: AgentOrchestrator,
    state: Any,
    *,
    thread_id: str | None = None,
    verbose: bool = False,
    compilation_elapsed_s: float | None = None,
) -> SessionResponse:
    """Execute a pre-compiled session state and return the final response."""
    from maivn._internal.utils.reporting.context import (
        current_reporter,
        current_sdk_delivery_mode,
        get_current_reporter,
        inside_orchestrator,
    )

    reporter, is_nested = _resolve_reporter(
        orch,
        verbose,
        get_current_reporter(),
        inside_orchestrator.get(),
    )

    orch._reporter = reporter
    _set_state_delivery_mode(state, "invoke")
    delivery_token = current_sdk_delivery_mode.set("invoke")
    token = current_reporter.set(reporter) if reporter else None
    orch_token = inside_orchestrator.set(True)

    orch._state = state
    if thread_id is not None:
        orch._thread_id = thread_id

    orch._interrupt_manager.clear_collected_interrupts()

    if reporter and not is_nested:
        reporter.print_section("Starting Execution")

    endpoints = orch._start_session(state)
    resolved_timeout = orch.agent.timeout if orch.agent.timeout is not None else orch.timeout

    final_payload: dict[str, Any] | None = None
    try:
        final_payload = _execute_session_with_reporter(
            orch,
            endpoints,
            resolved_timeout,
            reporter,
            is_nested,
        )
        if final_payload is None:
            raise RuntimeError("Received no payload from event stream.")
        response = SessionResponse.model_validate(final_payload)
        _report_completion(reporter, response, is_nested)
        return response
    except ValidationError as exc:
        raise RuntimeError(f"Failed to validate final session payload: {final_payload}") from exc
    finally:
        inside_orchestrator.reset(orch_token)
        current_sdk_delivery_mode.reset(delivery_token)
        if token is not None:
            current_reporter.reset(token)
        orch._progress_task = None
        _ = compilation_elapsed_s


# MARK: Stream Execution


def execute_stream(
    orch: AgentOrchestrator,
    state: Any,
    *,
    thread_id: str | None = None,
    verbose: bool = False,
    compilation_elapsed_s: float | None = None,
) -> Iterator[SSEEvent]:
    """Execute a pre-compiled state and stream raw SSE events."""
    from maivn._internal.utils.reporting.context import (
        current_sdk_delivery_mode,
        get_current_reporter,
        inside_orchestrator,
    )

    reporter, is_nested = _resolve_reporter(
        orch,
        verbose,
        get_current_reporter(),
        inside_orchestrator.get(),
    )

    orch._reporter = reporter
    _set_state_delivery_mode(state, "stream")
    orch._state = state
    if thread_id is not None:
        orch._thread_id = thread_id

    orch._interrupt_manager.clear_collected_interrupts()

    if reporter and not is_nested:
        reporter.print_section("Starting Execution")

    endpoints = orch._start_session(state)
    resolved_timeout = orch.agent.timeout if orch.agent.timeout is not None else orch.timeout

    stream_queue: queue.Queue[SSEEvent | object] = queue.Queue()
    stream_done = object()
    stream_error: BaseException | None = None

    def _on_event(event: SSEEvent) -> None:
        stream_queue.put(event)

    def _run_stream() -> None:
        nonlocal stream_error
        token = None
        delivery_token = None
        orch_token = None
        try:
            if reporter is not None:
                from maivn._internal.utils.reporting.context import current_reporter

                token = current_reporter.set(reporter)
            delivery_token = current_sdk_delivery_mode.set("stream")
            orch_token = inside_orchestrator.set(True)

            final_payload = _execute_session_with_reporter(
                orch,
                endpoints,
                resolved_timeout,
                reporter,
                is_nested,
                on_event=_on_event,
            )
            if final_payload is None:
                raise RuntimeError("Received no payload from event stream.")

            response = SessionResponse.model_validate(final_payload)
            _report_completion(reporter, response, is_nested)
        except BaseException as exc:  # noqa: BLE001
            stream_error = exc
        finally:
            if orch_token is not None:
                inside_orchestrator.reset(orch_token)
            if delivery_token is not None:
                current_sdk_delivery_mode.reset(delivery_token)
            if token is not None:
                from maivn._internal.utils.reporting.context import current_reporter

                current_reporter.reset(token)
            orch._progress_task = None
            stream_queue.put(stream_done)

    worker = threading.Thread(target=_run_stream, name="maivn-orchestrator-stream", daemon=True)
    worker.start()

    try:
        while True:
            queued_item = stream_queue.get()
            if queued_item is stream_done:
                break
            yield cast(SSEEvent, queued_item)
    finally:
        worker.join()
        _ = compilation_elapsed_s
        if stream_error is not None:
            raise stream_error


# MARK: Reporter Resolution


def _resolve_reporter(
    orch: AgentOrchestrator,
    verbose: bool,
    parent_reporter: BaseReporter | None,
    already_inside: bool,
) -> tuple[BaseReporter | None, bool]:
    """Resolve the reporter and nested state for an execution call."""
    reporter: BaseReporter | None = None
    is_nested = False

    if parent_reporter is not None:
        reporter = parent_reporter
        is_nested = already_inside
        if is_nested:
            reporter.print_section(f"Nested Agent: {orch.agent.name}")
        elif verbose:
            reporter.print_header(
                f"Agent: {orch.agent.name}",
                orch.agent.description or "Executing agent workflow",
            )
    elif verbose:
        from maivn._internal.utils.reporting import create_reporter

        reporter = create_reporter(enabled=True)
        reporter.print_header(
            f"Agent: {orch.agent.name}",
            orch.agent.description or "Executing agent workflow",
        )

    return reporter, is_nested


# MARK: Session Execution


def _execute_session_with_reporter(
    orch: AgentOrchestrator,
    endpoints: SessionEndpoints,
    timeout: float,
    reporter: BaseReporter | None,
    is_nested: bool,
    on_event: Callable[[SSEEvent], None] | None = None,
) -> dict[str, Any] | None:
    """Execute session and consume events, managing progress context."""
    from maivn._internal.utils.reporting.context import allow_nested_response_stream

    orch._reporter_hooks._is_nested = is_nested
    orch._reporter_hooks._allow_nested_response_stream = allow_nested_response_stream.get()

    if reporter and not is_nested:
        with reporter.live_progress("Processing events...") as task:
            orch._progress_task = task
            return orch._event_coordinator.consume_events(
                endpoints,
                timeout,
                reporter,
                task,
                on_event=on_event,
            )

    return orch._event_coordinator.consume_events(
        endpoints,
        timeout,
        reporter,
        None,
        on_event=on_event,
    )


# MARK: Completion Reporting


def _report_completion(
    reporter: BaseReporter | None,
    response: SessionResponse,
    is_nested: bool,
) -> None:
    """Report session completion via reporter."""
    if not reporter or is_nested:
        return

    if response.error:
        _report_error(reporter, response)
    else:
        reporter.print_event("success", "Agent execution completed successfully!")

    reporter.print_summary(token_usage=response.token_usage)
    final_response = extract_latest_response(response.responses)
    if final_response:
        reporter.print_final_response(final_response)
    reporter.print_final_result(response.result)


def _report_error(reporter: BaseReporter, response: SessionResponse) -> None:
    """Report error through reporter."""
    error_message = response.error or ""
    if "LLM payload contains private data values" in error_message:
        error_message = (
            f"{error_message} This happens when private_data values also appear "
            "in user-visible messages. Remove those values from the prompt or "
            "replace them with placeholders like {_{key}_}."
        )

    safe_message = sanitize_user_facing_error_message(error_message)
    session_suffix = (
        f" (Session ID: {response.session_id})" if getattr(response, "session_id", None) else ""
    )
    error_display = (
        f"Agent execution failed: {safe_message}{session_suffix}. Contact support if this persists."
    )
    reporter.print_event("error", error_display)


def _set_state_delivery_mode(state: Any, delivery_mode: str) -> None:
    """Persist the SDK delivery mode in session metadata for server-side routing."""
    if not hasattr(state, "metadata"):
        return

    metadata = getattr(state, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    else:
        metadata = dict(metadata)

    metadata["maivn_sdk_delivery_mode"] = delivery_mode
    state.metadata = metadata


__all__ = ["execute_invoke", "execute_stream"]
