"""Forward normalized AppEvents into terminal-style reporters."""

from __future__ import annotations

import inspect
from typing import Any

from ..._internal.core.orchestrator.helpers import sanitize_user_facing_error_message
from .._models import AppEvent
from .payload import (
    coerce_mapping,
    extract_tool_payload,
    mapping_value,
    normalize_tool_status,
    normalize_tool_type,
    normalized_text,
    string_value,
)
from .state import NormalizedEventForwardingState, clear_tool_state, remember_tool_context

# MARK: Dispatcher


def forward_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    event_name = normalized_text(event.event_name) or ""

    if event_name == "session_start":
        _forward_session_start(event, payload=payload, reporter=reporter)
        return
    if event_name == "assistant_chunk":
        _forward_assistant_chunk(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "status_message":
        _forward_status_message(event, payload=payload, reporter=reporter)
        return
    if event_name == "agent_assignment":
        _forward_agent_assignment(event, payload=payload, reporter=reporter)
        return
    if event_name == "enrichment":
        _forward_enrichment(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "tool_event":
        _forward_tool_event(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "system_tool_start":
        _forward_system_tool_start(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "system_tool_chunk":
        _forward_system_tool_chunk(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "system_tool_complete":
        _forward_system_tool_complete(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "system_tool_error":
        _forward_system_tool_error(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "final":
        _forward_final(event, payload=payload, reporter=reporter)
        return
    if event_name == "error":
        _forward_error(event, payload=payload, reporter=reporter)


# MARK: Session and Assistant Events


def _forward_session_start(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    session_id = normalized_text(payload.get("session_id")) or normalized_text(
        getattr(event.session, "id", None)
    )
    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.session, "assistant_id", None)
    )
    if session_id and assistant_id:
        reporter.report_session_start(session_id, assistant_id)


def _forward_assistant_chunk(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    delta = string_value(payload.get("text")) or string_value(
        getattr(event.assistant, "delta", None)
    )
    if not delta:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.assistant, "id", None)
    )
    stream_id = assistant_id or "assistant"
    previous = state.assistant_text_by_id.get(stream_id, "")
    full_text = previous + delta
    state.assistant_text_by_id[stream_id] = full_text

    reporter.report_response_chunk(
        delta,
        assistant_id=stream_id,
        full_text=full_text,
    )


def _forward_status_message(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    message = string_value(payload.get("message")) or string_value(
        mapping_value(payload.get("status"), "message")
    )
    if not message:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.assistant, "id", None)
    )
    reporter.report_status_message(message, assistant_id=assistant_id or "assistant")


# MARK: Assignment and Enrichment


def _forward_agent_assignment(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    report_agent_assignment = getattr(reporter, "report_agent_assignment", None)
    if not callable(report_agent_assignment):
        return

    assignment_id = normalized_text(payload.get("assignment_id")) or normalized_text(
        getattr(event.assignment, "id", None)
    )
    agent_name = normalized_text(payload.get("agent_name")) or normalized_text(
        getattr(event.assignment, "agent_name", None)
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        getattr(event.assignment, "status", None)
    )
    swarm_name = normalized_text(payload.get("swarm_name")) or normalized_text(
        getattr(event.assignment, "swarm_name", None)
    )
    error = normalized_text(payload.get("error")) or normalized_text(
        getattr(event.assignment, "error", None)
    )
    result = payload.get("result", getattr(event.assignment, "result", None))

    if not agent_name or not status:
        return

    report_agent_assignment(
        agent_name=agent_name,
        status=status,
        assignment_id=assignment_id or f"agent:{agent_name}",
        swarm_name=swarm_name,
        error=error,
        result=result,
    )


def _forward_enrichment(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    phase = normalized_text(payload.get("phase")) or normalized_text(
        getattr(event.enrichment, "phase", None)
    )
    message = normalized_text(payload.get("message")) or normalized_text(
        getattr(event.enrichment, "message", None)
    )
    if not phase:
        return

    report_enrichment = getattr(reporter, "report_enrichment", None)
    if not callable(report_enrichment):
        report_phase_change = getattr(reporter, "report_phase_change", None)
        if callable(report_phase_change):
            report_phase_change(phase)
        return

    scope_id = normalized_text(payload.get("scope_id")) or normalized_text(
        getattr(event.scope, "id", None)
    )
    scope_name = normalized_text(payload.get("scope_name")) or normalized_text(
        getattr(event.scope, "name", None)
    )
    scope_type = normalized_text(payload.get("scope_type")) or normalized_text(
        getattr(event.scope, "type", None)
    )
    memory = coerce_mapping(payload.get("memory")) or coerce_mapping(
        getattr(event.enrichment, "memory", None)
    )
    redaction = coerce_mapping(payload.get("redaction")) or coerce_mapping(
        getattr(event.enrichment, "redaction", None)
    )
    supports_scope, supports_memory, supports_redaction = _get_enrichment_support(
        reporter,
        state=state,
    )
    kwargs: dict[str, Any] = {
        "phase": phase,
        "message": message or phase,
    }
    if supports_scope:
        kwargs["scope_id"] = scope_id
        kwargs["scope_name"] = scope_name
        kwargs["scope_type"] = scope_type
    if supports_memory and memory is not None:
        kwargs["memory"] = memory
    if supports_redaction and redaction is not None:
        kwargs["redaction"] = redaction
    report_enrichment(**kwargs)


# MARK: Tool Events


def _forward_tool_event(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool = extract_tool_payload(event, payload=payload)
    if not tool.tool_id or not tool.tool_name or not tool.status:
        return

    normalized_status = normalize_tool_status(tool.status)
    normalized_type = normalize_tool_type(tool.tool_type)
    if normalized_type == "system":
        remember_tool_context(
            state,
            tool_id=tool.tool_id,
            tool_name=tool.tool_name,
            tool_type=normalized_type,
            agent_name=tool.agent_name,
            swarm_name=tool.swarm_name,
        )
        if normalized_status == "executing":
            reporter.report_tool_start(
                tool.tool_name,
                tool.tool_id,
                normalized_type,
                tool.agent_name,
                tool.args,
                tool.swarm_name,
            )
            return
        if normalized_status == "completed":
            reporter.report_tool_complete(tool.tool_id, result=tool.result)
            clear_tool_state(state, tool.tool_id)
            return
        if normalized_status == "failed":
            reporter.report_tool_error(
                tool.tool_name, tool.error or "Unknown error", event_id=tool.tool_id
            )
            clear_tool_state(state, tool.tool_id)
            return
        return

    if normalized_type == "model":
        if normalized_status == "completed":
            reporter.report_model_tool_complete(
                tool.tool_name,
                event_id=tool.tool_id,
                agent_name=tool.agent_name,
                swarm_name=tool.swarm_name,
                result=tool.result,
            )
            clear_tool_state(state, tool.tool_id)
            return
        if normalized_status == "failed":
            reporter.report_tool_error(
                tool.tool_name, tool.error or "Unknown error", event_id=tool.tool_id
            )
            clear_tool_state(state, tool.tool_id)
            return
        remember_tool_context(
            state,
            tool_id=tool.tool_id,
            tool_name=tool.tool_name,
            tool_type=normalized_type,
            agent_name=tool.agent_name,
            swarm_name=tool.swarm_name,
        )
        return

    remember_tool_context(
        state,
        tool_id=tool.tool_id,
        tool_name=tool.tool_name,
        tool_type=normalized_type,
        agent_name=tool.agent_name,
        swarm_name=tool.swarm_name,
    )
    if normalized_status == "executing":
        reporter.report_tool_start(
            tool.tool_name,
            tool.tool_id,
            normalized_type,
            tool.agent_name,
            tool.args,
            tool.swarm_name,
        )
        return
    if normalized_status == "completed":
        reporter.report_tool_complete(tool.tool_id, result=tool.result)
        clear_tool_state(state, tool.tool_id)
        return
    if normalized_status == "failed":
        reporter.report_tool_error(
            tool.tool_name, tool.error or "Unknown error", event_id=tool.tool_id
        )
        clear_tool_state(state, tool.tool_id)


def _forward_system_tool_start(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool = extract_tool_payload(event, payload=payload)
    if not tool.tool_id or not tool.tool_name:
        return

    remember_tool_context(
        state,
        tool_id=tool.tool_id,
        tool_name=tool.tool_name,
        tool_type="system",
        agent_name=tool.agent_name,
        swarm_name=tool.swarm_name,
    )
    reporter.report_tool_start(
        tool.tool_name,
        tool.tool_id,
        "system",
        tool.agent_name,
        tool.args,
        tool.swarm_name,
    )


def _forward_system_tool_chunk(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    context = state.tool_context_by_id.get(tool_id)
    tool_name = context.name if context is not None else "system_tool"
    chunk_text = string_value(payload.get("text")) or string_value(
        getattr(event.chunk, "text", None)
    )
    chunk_count = state.system_tool_chunk_count_by_id.get(tool_id, 0) + 1
    state.system_tool_chunk_count_by_id[tool_id] = chunk_count

    reporter.report_system_tool_progress(
        event_id=tool_id,
        tool_name=tool_name,
        chunk_count=chunk_count,
        elapsed_seconds=0.0,
        text=chunk_text,
    )


def _forward_system_tool_complete(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    reporter.report_tool_complete(
        tool_id,
        result=payload.get("result", getattr(event.tool, "result", None)),
    )
    clear_tool_state(state, tool_id)


def _forward_system_tool_error(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    context = state.tool_context_by_id.get(tool_id)
    tool_name = (
        context.name
        if context is not None
        else (normalized_text(payload.get("tool_name")) or "system_tool")
    )
    error = normalized_text(payload.get("error")) or normalized_text(
        getattr(event.tool, "error", None)
    )
    reporter.report_tool_error(tool_name, error or "Unknown error", event_id=tool_id)
    clear_tool_state(state, tool_id)


# MARK: Terminal Events


def _forward_final(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    reporter.print_event("success", "Agent execution completed successfully!")
    token_usage = coerce_mapping(payload.get("token_usage")) or coerce_mapping(
        getattr(event.output, "token_usage", None)
    )
    reporter.print_summary(token_usage=token_usage)

    response = string_value(payload.get("response")) or string_value(
        getattr(event.output, "response", None)
    )
    if response:
        reporter.print_final_response(response)
    reporter.print_final_result(payload.get("result", getattr(event.output, "result", None)))


def _forward_error(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    error_text = string_value(payload.get("error")) or string_value(
        getattr(event.error_info, "message", None)
    )
    if not error_text:
        error_text = "Unknown error"

    safe_message = sanitize_user_facing_error_message(error_text)
    reporter.print_event(
        "error",
        f"Agent execution failed: {safe_message}. Contact support if this persists.",
    )


# MARK: Introspection


def _get_enrichment_support(
    reporter: Any,
    *,
    state: NormalizedEventForwardingState,
) -> tuple[bool, bool, bool]:
    reporter_type = type(reporter)
    cached = state.enrichment_support_by_reporter_type.get(reporter_type)
    if cached is not None:
        return cached

    try:
        params = inspect.signature(reporter.report_enrichment).parameters
    except (AttributeError, TypeError, ValueError):
        cached = (False, False, False)
    else:
        accepts_var_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
        )
        cached = (
            accepts_var_kwargs or "scope_id" in params,
            accepts_var_kwargs or "memory" in params,
            accepts_var_kwargs or "redaction" in params,
        )

    state.enrichment_support_by_reporter_type[reporter_type] = cached
    return cached


__all__ = ["forward_to_reporter"]
