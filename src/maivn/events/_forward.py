"""Public helpers for replaying normalized AppEvents into reporters and bridges."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .._internal.core.orchestrator.helpers import sanitize_user_facing_error_message
from ._bridge import EventBridge
from ._models import AppEvent

# MARK: - State


@dataclass
class _ToolContext:
    name: str
    tool_type: str
    agent_name: str | None = None
    swarm_name: str | None = None


@dataclass
class NormalizedEventForwardingState:
    """Per-stream state for normalized event forwarding."""

    assistant_text_by_id: dict[str, str] = field(default_factory=dict)
    tool_context_by_id: dict[str, _ToolContext] = field(default_factory=dict)
    system_tool_chunk_count_by_id: dict[str, int] = field(default_factory=dict)
    enrichment_support_by_reporter_type: dict[type[Any], tuple[bool, bool, bool]] = field(
        default_factory=dict
    )


# MARK: - Public API


async def forward_normalized_event(
    event: AppEvent,
    *,
    reporter: Any | None = None,
    bridge: EventBridge | None = None,
    state: NormalizedEventForwardingState | None = None,
) -> NormalizedEventForwardingState:
    """Forward a normalized AppEvent into a reporter and/or UI bridge."""
    active_state = state or NormalizedEventForwardingState()
    payload = event.model_dump(mode="python")

    if reporter is not None:
        _forward_to_reporter(event, payload=payload, reporter=reporter, state=active_state)
    if bridge is not None:
        await _forward_to_bridge(event, payload=payload, bridge=bridge, state=active_state)

    return active_state


async def forward_normalized_stream(
    events: Iterable[AppEvent],
    *,
    reporter: Any | None = None,
    bridge: EventBridge | None = None,
    state: NormalizedEventForwardingState | None = None,
) -> NormalizedEventForwardingState:
    """Forward a normalized AppEvent stream with shared streaming state."""
    active_state = state or NormalizedEventForwardingState()
    for event in events:
        await forward_normalized_event(
            event,
            reporter=reporter,
            bridge=bridge,
            state=active_state,
        )
    return active_state


# MARK: - Reporter Forwarding


def _forward_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    event_name = _normalized_text(event.event_name) or ""

    if event_name == "session_start":
        _forward_session_start_to_reporter(event, payload=payload, reporter=reporter)
        return
    if event_name == "assistant_chunk":
        _forward_assistant_chunk_to_reporter(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "status_message":
        _forward_status_message_to_reporter(event, payload=payload, reporter=reporter)
        return
    if event_name == "agent_assignment":
        _forward_agent_assignment_to_reporter(event, payload=payload, reporter=reporter)
        return
    if event_name == "enrichment":
        _forward_enrichment_to_reporter(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "tool_event":
        _forward_tool_event_to_reporter(event, payload=payload, reporter=reporter, state=state)
        return
    if event_name == "system_tool_start":
        _forward_system_tool_start_to_reporter(
            event,
            payload=payload,
            reporter=reporter,
            state=state,
        )
        return
    if event_name == "system_tool_chunk":
        _forward_system_tool_chunk_to_reporter(
            event,
            payload=payload,
            reporter=reporter,
            state=state,
        )
        return
    if event_name == "system_tool_complete":
        _forward_system_tool_complete_to_reporter(
            event,
            payload=payload,
            reporter=reporter,
            state=state,
        )
        return
    if event_name == "system_tool_error":
        _forward_system_tool_error_to_reporter(
            event,
            payload=payload,
            reporter=reporter,
            state=state,
        )
        return
    if event_name == "final":
        _forward_final_to_reporter(event, payload=payload, reporter=reporter)
        return
    if event_name == "error":
        _forward_error_to_reporter(event, payload=payload, reporter=reporter)


def _forward_session_start_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    session_id = _normalized_text(payload.get("session_id")) or _normalized_text(
        getattr(event.session, "id", None)
    )
    assistant_id = _normalized_text(payload.get("assistant_id")) or _normalized_text(
        getattr(event.session, "assistant_id", None)
    )
    if session_id and assistant_id:
        reporter.report_session_start(session_id, assistant_id)


def _forward_assistant_chunk_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    delta = _string_value(payload.get("text")) or _string_value(
        getattr(event.assistant, "delta", None)
    )
    if not delta:
        return

    assistant_id = _normalized_text(payload.get("assistant_id")) or _normalized_text(
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


def _forward_status_message_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    message = _string_value(payload.get("message")) or _string_value(
        _mapping_value(payload.get("status"), "message")
    )
    if not message:
        return

    assistant_id = _normalized_text(payload.get("assistant_id")) or _normalized_text(
        getattr(event.assistant, "id", None)
    )
    reporter.report_status_message(message, assistant_id=assistant_id or "assistant")


def _forward_agent_assignment_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    report_agent_assignment = getattr(reporter, "report_agent_assignment", None)
    if not callable(report_agent_assignment):
        return

    assignment_id = _normalized_text(payload.get("assignment_id")) or _normalized_text(
        getattr(event.assignment, "id", None)
    )
    agent_name = _normalized_text(payload.get("agent_name")) or _normalized_text(
        getattr(event.assignment, "agent_name", None)
    )
    status = _normalized_text(payload.get("status")) or _normalized_text(
        getattr(event.assignment, "status", None)
    )
    swarm_name = _normalized_text(payload.get("swarm_name")) or _normalized_text(
        getattr(event.assignment, "swarm_name", None)
    )
    error = _normalized_text(payload.get("error")) or _normalized_text(
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


def _forward_enrichment_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    phase = _normalized_text(payload.get("phase")) or _normalized_text(
        getattr(event.enrichment, "phase", None)
    )
    message = _normalized_text(payload.get("message")) or _normalized_text(
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

    scope_id = _normalized_text(payload.get("scope_id")) or _normalized_text(
        getattr(event.scope, "id", None)
    )
    scope_name = _normalized_text(payload.get("scope_name")) or _normalized_text(
        getattr(event.scope, "name", None)
    )
    scope_type = _normalized_text(payload.get("scope_type")) or _normalized_text(
        getattr(event.scope, "type", None)
    )
    memory = _coerce_mapping(payload.get("memory")) or _coerce_mapping(
        getattr(event.enrichment, "memory", None)
    )
    redaction = _coerce_mapping(payload.get("redaction")) or _coerce_mapping(
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


def _forward_tool_event_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id, tool_name, tool_type, status, args, result, error, agent_name, swarm_name = (
        _extract_tool_payload(event, payload=payload)
    )
    if not tool_id or not tool_name or not status:
        return

    normalized_status = _normalize_tool_status(status)
    normalized_type = _normalize_tool_type(tool_type)
    if normalized_type == "system":
        _remember_tool_context(
            state,
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=normalized_type,
            agent_name=agent_name,
            swarm_name=swarm_name,
        )
        if normalized_status == "executing":
            reporter.report_tool_start(
                tool_name,
                tool_id,
                normalized_type,
                agent_name,
                args,
                swarm_name,
            )
            return
        if normalized_status == "completed":
            reporter.report_tool_complete(tool_id, result=result)
            _clear_tool_state(state, tool_id)
            return
        if normalized_status == "failed":
            reporter.report_tool_error(tool_name, error or "Unknown error", event_id=tool_id)
            _clear_tool_state(state, tool_id)
            return
        return

    if normalized_type == "model":
        if normalized_status == "completed":
            reporter.report_model_tool_complete(
                tool_name,
                event_id=tool_id,
                agent_name=agent_name,
                swarm_name=swarm_name,
                result=result,
            )
            _clear_tool_state(state, tool_id)
            return
        if normalized_status == "failed":
            reporter.report_tool_error(tool_name, error or "Unknown error", event_id=tool_id)
            _clear_tool_state(state, tool_id)
            return
        _remember_tool_context(
            state,
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=normalized_type,
            agent_name=agent_name,
            swarm_name=swarm_name,
        )
        return

    _remember_tool_context(
        state,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type=normalized_type,
        agent_name=agent_name,
        swarm_name=swarm_name,
    )
    if normalized_status == "executing":
        reporter.report_tool_start(
            tool_name,
            tool_id,
            normalized_type,
            agent_name,
            args,
            swarm_name,
        )
        return
    if normalized_status == "completed":
        reporter.report_tool_complete(tool_id, result=result)
        _clear_tool_state(state, tool_id)
        return
    if normalized_status == "failed":
        reporter.report_tool_error(tool_name, error or "Unknown error", event_id=tool_id)
        _clear_tool_state(state, tool_id)


def _forward_system_tool_start_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id, tool_name, _, _, args, _, _, agent_name, swarm_name = _extract_tool_payload(
        event,
        payload=payload,
    )
    if not tool_id or not tool_name:
        return

    _remember_tool_context(
        state,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type="system",
        agent_name=agent_name,
        swarm_name=swarm_name,
    )
    reporter.report_tool_start(
        tool_name,
        tool_id,
        "system",
        agent_name,
        args,
        swarm_name,
    )


def _forward_system_tool_chunk_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = _normalized_text(payload.get("tool_id")) or _normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    context = state.tool_context_by_id.get(tool_id)
    tool_name = context.name if context is not None else "system_tool"
    chunk_text = _string_value(payload.get("text")) or _string_value(
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


def _forward_system_tool_complete_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = _normalized_text(payload.get("tool_id")) or _normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    reporter.report_tool_complete(
        tool_id,
        result=payload.get("result", getattr(event.tool, "result", None)),
    )
    _clear_tool_state(state, tool_id)


def _forward_system_tool_error_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = _normalized_text(payload.get("tool_id")) or _normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    context = state.tool_context_by_id.get(tool_id)
    tool_name = (
        context.name
        if context is not None
        else (_normalized_text(payload.get("tool_name")) or "system_tool")
    )
    error = _normalized_text(payload.get("error")) or _normalized_text(
        getattr(event.tool, "error", None)
    )
    reporter.report_tool_error(tool_name, error or "Unknown error", event_id=tool_id)
    _clear_tool_state(state, tool_id)


def _forward_final_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    reporter.print_event("success", "Agent execution completed successfully!")
    token_usage = _coerce_mapping(payload.get("token_usage")) or _coerce_mapping(
        getattr(event.output, "token_usage", None)
    )
    reporter.print_summary(token_usage=token_usage)

    response = _string_value(payload.get("response")) or _string_value(
        getattr(event.output, "response", None)
    )
    if response:
        reporter.print_final_response(response)
    reporter.print_final_result(payload.get("result", getattr(event.output, "result", None)))


def _forward_error_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
) -> None:
    error_text = _string_value(payload.get("error")) or _string_value(
        getattr(event.error_info, "message", None)
    )
    if not error_text:
        error_text = "Unknown error"

    safe_message = sanitize_user_facing_error_message(error_text)
    reporter.print_event(
        "error",
        f"Agent execution failed: {safe_message}. Contact support if this persists.",
    )


# MARK: - Bridge Forwarding


async def _forward_to_bridge(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    event_name = _normalized_text(event.event_name) or ""

    if event_name == "assistant_chunk":
        text = _string_value(payload.get("text")) or _string_value(
            getattr(event.assistant, "delta", None)
        )
        if text:
            assistant_id = _normalized_text(payload.get("assistant_id")) or _normalized_text(
                getattr(event.assistant, "id", None)
            )
            await bridge.emit_assistant_chunk(assistant_id=assistant_id or "assistant", text=text)
        return

    if event_name == "status_message":
        message = _string_value(payload.get("message")) or _string_value(
            _mapping_value(payload.get("status"), "message")
        )
        if message:
            assistant_id = _normalized_text(payload.get("assistant_id")) or _normalized_text(
                getattr(event.assistant, "id", None)
            )
            await bridge.emit_status_message(
                assistant_id=assistant_id or "assistant",
                message=message,
            )
        return

    if event_name == "interrupt_required":
        interrupt = event.interrupt
        interrupt_id = _normalized_text(payload.get("interrupt_id")) or _normalized_text(
            getattr(interrupt, "id", None)
        )
        data_key = _normalized_text(payload.get("data_key")) or _normalized_text(
            getattr(interrupt, "data_key", None)
        )
        prompt = _string_value(payload.get("prompt")) or _string_value(
            getattr(interrupt, "prompt", None)
        )
        if interrupt_id and data_key and prompt:
            await bridge.emit_interrupt_required(
                interrupt_id=interrupt_id,
                checkpoint_id=_normalized_text(payload.get("checkpoint_id"))
                or _normalized_text(getattr(interrupt, "checkpoint_id", None)),
                data_key=data_key,
                prompt=prompt,
                tool_name=_normalized_text(payload.get("tool_name"))
                or _normalized_text(getattr(interrupt, "tool_name", None)),
                arg_name=_normalized_text(payload.get("arg_name"))
                or _normalized_text(getattr(interrupt, "arg_name", None)),
                assignment_id=_normalized_text(payload.get("assignment_id"))
                or _normalized_text(getattr(interrupt, "assignment_id", None)),
                interrupt_number=_integer_value(payload.get("interrupt_number"))
                or _integer_value(getattr(interrupt, "number", None))
                or 1,
                total_interrupts=_integer_value(payload.get("total_interrupts"))
                or _integer_value(getattr(interrupt, "total", None))
                or 1,
                input_type=_normalized_text(payload.get("input_type"))
                or _normalized_text(getattr(interrupt, "input_type", None))
                or "text",
                choices=_string_list(payload.get("choices"))
                or _string_list(getattr(interrupt, "choices", None)),
            )
        return

    if event_name == "agent_assignment":
        assignment = event.assignment
        agent_name = _normalized_text(payload.get("agent_name")) or _normalized_text(
            getattr(assignment, "agent_name", None)
        )
        status = _normalized_text(payload.get("status")) or _normalized_text(
            getattr(assignment, "status", None)
        )
        if agent_name and status:
            await bridge.emit_agent_assignment(
                agent_name=agent_name,
                status=status,
                assignment_id=_normalized_text(payload.get("assignment_id"))
                or _normalized_text(getattr(assignment, "id", None)),
                swarm_name=_normalized_text(payload.get("swarm_name"))
                or _normalized_text(getattr(assignment, "swarm_name", None)),
                task=_normalized_text(payload.get("task"))
                or _normalized_text(getattr(assignment, "task", None)),
                error=_normalized_text(payload.get("error"))
                or _normalized_text(getattr(assignment, "error", None)),
                result=payload.get("result", getattr(assignment, "result", None)),
            )
        return

    if event_name == "enrichment":
        enrichment = event.enrichment
        phase = _normalized_text(payload.get("phase")) or _normalized_text(
            getattr(enrichment, "phase", None)
        )
        message = _normalized_text(payload.get("message")) or _normalized_text(
            getattr(enrichment, "message", None)
        )
        if phase:
            await bridge.emit_enrichment(
                phase=phase,
                message=message or phase,
                scope_id=_normalized_text(payload.get("scope_id"))
                or _normalized_text(getattr(event.scope, "id", None)),
                scope_name=_normalized_text(payload.get("scope_name"))
                or _normalized_text(getattr(event.scope, "name", None)),
                scope_type=_normalized_text(payload.get("scope_type"))
                or _normalized_text(getattr(event.scope, "type", None)),
                memory=_coerce_mapping(payload.get("memory"))
                or _coerce_mapping(getattr(enrichment, "memory", None)),
                redaction=_coerce_mapping(payload.get("redaction"))
                or _coerce_mapping(getattr(enrichment, "redaction", None)),
            )
        return

    if event_name == "tool_event":
        await _forward_tool_event_to_bridge(event, payload=payload, bridge=bridge, state=state)
        return

    if event_name == "system_tool_start":
        tool_id, tool_name, _, _, args, _, _, agent_name, swarm_name = _extract_tool_payload(
            event,
            payload=payload,
        )
        if tool_id and tool_name:
            _remember_tool_context(
                state,
                tool_id=tool_id,
                tool_name=tool_name,
                tool_type="system",
                agent_name=agent_name,
                swarm_name=swarm_name,
            )
            await bridge.emit_system_tool_start(
                tool_type=tool_name,
                tool_id=tool_id,
                params=args,
                agent_name=agent_name,
                swarm_name=swarm_name,
            )
        return

    if event_name == "system_tool_chunk":
        tool_id = _normalized_text(payload.get("tool_id")) or _normalized_text(
            getattr(event.tool, "id", None)
        )
        text = _string_value(payload.get("text")) or _string_value(
            getattr(event.chunk, "text", None)
        )
        if tool_id and text is not None:
            await bridge.emit_system_tool_chunk(
                tool_id=tool_id,
                text=text,
                progress=_float_value(payload.get("progress"))
                or _float_value(getattr(event.chunk, "progress", None)),
            )
        return

    if event_name == "system_tool_complete":
        tool_id = _normalized_text(payload.get("tool_id")) or _normalized_text(
            getattr(event.tool, "id", None)
        )
        if tool_id:
            await bridge.emit_system_tool_complete(
                tool_id=tool_id,
                result=payload.get("result", getattr(event.tool, "result", None)),
            )
            _clear_tool_state(state, tool_id)
        return

    if event_name == "system_tool_error":
        tool_id = _normalized_text(payload.get("tool_id")) or _normalized_text(
            getattr(event.tool, "id", None)
        )
        context = state.tool_context_by_id.get(tool_id or "")
        tool_name = (
            context.name
            if context is not None
            else (_normalized_text(payload.get("tool_name")) or "system_tool")
        )
        if tool_id:
            await bridge.emit_tool_event(
                tool_name=tool_name,
                tool_id=tool_id,
                status="failed",
                error=_normalized_text(payload.get("error"))
                or _normalized_text(getattr(event.tool, "error", None)),
                tool_type="system",
                agent_name=context.agent_name if context is not None else None,
                swarm_name=context.swarm_name if context is not None else None,
            )
            _clear_tool_state(state, tool_id)
        return

    await bridge.emit(event_name, payload)


async def _forward_tool_event_to_bridge(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id, tool_name, tool_type, status, args, result, error, agent_name, swarm_name = (
        _extract_tool_payload(event, payload=payload)
    )
    if not tool_id or not tool_name or not status:
        return

    normalized_status = _normalize_tool_status(status)
    normalized_type = _normalize_tool_type(tool_type)
    _remember_tool_context(
        state,
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type=normalized_type,
        agent_name=agent_name,
        swarm_name=swarm_name,
    )

    if normalized_type == "system":
        if normalized_status == "executing":
            await bridge.emit_system_tool_start(
                tool_type=tool_name,
                tool_id=tool_id,
                params=args,
                agent_name=agent_name,
                swarm_name=swarm_name,
            )
            return
        if normalized_status == "completed":
            await bridge.emit_system_tool_complete(tool_id=tool_id, result=result)
            _clear_tool_state(state, tool_id)
            return
        if normalized_status == "failed":
            await bridge.emit_tool_event(
                tool_name=tool_name,
                tool_id=tool_id,
                status="failed",
                args=args,
                result=result,
                error=error,
                agent_name=agent_name,
                swarm_name=swarm_name,
                tool_type="system",
            )
            _clear_tool_state(state, tool_id)
        return

    if normalized_type == "model":
        if normalized_status in {"completed", "failed"}:
            await bridge.emit_tool_event(
                tool_name=tool_name,
                tool_id=tool_id,
                status=normalized_status,
                args=args,
                result=result,
                error=error,
                agent_name=agent_name,
                swarm_name=swarm_name,
                tool_type="model",
            )
            _clear_tool_state(state, tool_id)
        return

    await bridge.emit_tool_event(
        tool_name=tool_name,
        tool_id=tool_id,
        status=normalized_status,
        args=args,
        result=result,
        error=error,
        agent_name=agent_name,
        swarm_name=swarm_name,
        tool_type=normalized_type,
    )
    if normalized_status in {"completed", "failed"}:
        _clear_tool_state(state, tool_id)


# MARK: - Helpers


def _extract_tool_payload(
    event: AppEvent,
    *,
    payload: dict[str, Any],
) -> tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    dict[str, Any] | None,
    Any,
    str | None,
    str | None,
    str | None,
]:
    tool = event.tool
    tool_id = (
        _normalized_text(payload.get("tool_id"))
        or _normalized_text(payload.get("event_id"))
        or _normalized_text(getattr(tool, "id", None))
    )
    tool_name = (
        _normalized_text(payload.get("tool_name"))
        or _normalized_text(payload.get("tool_type"))
        or _normalized_text(getattr(tool, "name", None))
    )
    tool_type = _normalized_text(payload.get("tool_type")) or _normalized_text(
        getattr(tool, "type", None)
    )
    status = _normalized_text(payload.get("status")) or _normalized_text(
        getattr(tool, "status", None)
    )
    args = _coerce_mapping(payload.get("args")) or _coerce_mapping(payload.get("params"))
    if args is None and tool is not None:
        args = dict(tool.args)
    result = payload.get("result", getattr(tool, "result", None))
    error = _normalized_text(payload.get("error")) or _normalized_text(getattr(tool, "error", None))
    agent_name = _normalized_text(payload.get("agent_name"))
    swarm_name = _normalized_text(payload.get("swarm_name"))
    if agent_name is None and getattr(event.scope, "type", None) == "agent":
        agent_name = _normalized_text(getattr(event.scope, "name", None))
    if swarm_name is None and getattr(event.scope, "type", None) == "swarm":
        swarm_name = _normalized_text(getattr(event.scope, "name", None))
    return tool_id, tool_name, tool_type, status, args, result, error, agent_name, swarm_name


def _remember_tool_context(
    state: NormalizedEventForwardingState,
    *,
    tool_id: str,
    tool_name: str,
    tool_type: str,
    agent_name: str | None,
    swarm_name: str | None,
) -> None:
    state.tool_context_by_id[tool_id] = _ToolContext(
        name=tool_name,
        tool_type=tool_type,
        agent_name=agent_name,
        swarm_name=swarm_name,
    )


def _clear_tool_state(state: NormalizedEventForwardingState, tool_id: str) -> None:
    state.tool_context_by_id.pop(tool_id, None)
    state.system_tool_chunk_count_by_id.pop(tool_id, None)


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


def _normalized_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return dict(value)


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _integer_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _float_value(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _normalize_tool_type(tool_type: str | None) -> str:
    return (_normalized_text(tool_type) or "func").lower()


def _normalize_tool_status(status: str | None) -> str:
    normalized = (_normalized_text(status) or "executing").lower()
    if normalized in {"started", "running", "in_progress", "pending"}:
        return "executing"
    if normalized in {"completed", "success"}:
        return "completed"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized


__all__ = [
    "NormalizedEventForwardingState",
    "forward_normalized_event",
    "forward_normalized_stream",
]
