"""Forward normalized AppEvents into UI event bridges."""

from __future__ import annotations

from typing import Any

from .._bridge import EventBridge
from .._models import AppEvent
from .payload import (
    coerce_mapping,
    extract_tool_payload,
    float_value,
    integer_value,
    mapping_value,
    normalize_tool_status,
    normalize_tool_type,
    normalized_text,
    string_list,
    string_value,
)
from .state import NormalizedEventForwardingState, clear_tool_state, remember_tool_context

# MARK: Dispatcher


async def forward_to_bridge(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    event_name = normalized_text(event.event_name) or ""

    if event_name == "assistant_chunk":
        await _forward_assistant_chunk(event, payload=payload, bridge=bridge)
        return
    if event_name == "status_message":
        await _forward_status_message(event, payload=payload, bridge=bridge)
        return
    if event_name == "interrupt_required":
        await _forward_interrupt_required(event, payload=payload, bridge=bridge)
        return
    if event_name == "agent_assignment":
        await _forward_agent_assignment(event, payload=payload, bridge=bridge)
        return
    if event_name == "enrichment":
        await _forward_enrichment(event, payload=payload, bridge=bridge)
        return
    if event_name == "tool_event":
        await _forward_tool_event(event, payload=payload, bridge=bridge, state=state)
        return
    if event_name == "system_tool_start":
        await _forward_system_tool_start(event, payload=payload, bridge=bridge, state=state)
        return
    if event_name == "system_tool_chunk":
        await _forward_system_tool_chunk(event, payload=payload, bridge=bridge)
        return
    if event_name == "system_tool_complete":
        await _forward_system_tool_complete(event, payload=payload, bridge=bridge, state=state)
        return
    if event_name == "system_tool_error":
        await _forward_system_tool_error(event, payload=payload, bridge=bridge, state=state)
        return

    await bridge.emit(event_name, payload)


# MARK: Assistant Events


async def _forward_assistant_chunk(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
) -> None:
    text = string_value(payload.get("text")) or string_value(
        getattr(event.assistant, "delta", None)
    )
    if not text:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.assistant, "id", None)
    )
    await bridge.emit_assistant_chunk(assistant_id=assistant_id or "assistant", text=text)


async def _forward_status_message(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
) -> None:
    message = string_value(payload.get("message")) or string_value(
        mapping_value(payload.get("status"), "message")
    )
    if not message:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        getattr(event.assistant, "id", None)
    )
    await bridge.emit_status_message(
        assistant_id=assistant_id or "assistant",
        message=message,
    )


# MARK: Interrupts


async def _forward_interrupt_required(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
) -> None:
    interrupt = event.interrupt
    interrupt_id = normalized_text(payload.get("interrupt_id")) or normalized_text(
        getattr(interrupt, "id", None)
    )
    data_key = normalized_text(payload.get("data_key")) or normalized_text(
        getattr(interrupt, "data_key", None)
    )
    prompt = string_value(payload.get("prompt")) or string_value(getattr(interrupt, "prompt", None))
    if not interrupt_id or not data_key or not prompt:
        return

    await bridge.emit_interrupt_required(
        interrupt_id=interrupt_id,
        checkpoint_id=normalized_text(payload.get("checkpoint_id"))
        or normalized_text(getattr(interrupt, "checkpoint_id", None)),
        data_key=data_key,
        prompt=prompt,
        tool_name=normalized_text(payload.get("tool_name"))
        or normalized_text(getattr(interrupt, "tool_name", None)),
        arg_name=normalized_text(payload.get("arg_name"))
        or normalized_text(getattr(interrupt, "arg_name", None)),
        assignment_id=normalized_text(payload.get("assignment_id"))
        or normalized_text(getattr(interrupt, "assignment_id", None)),
        interrupt_number=integer_value(payload.get("interrupt_number"))
        or integer_value(getattr(interrupt, "number", None))
        or 1,
        total_interrupts=integer_value(payload.get("total_interrupts"))
        or integer_value(getattr(interrupt, "total", None))
        or 1,
        input_type=normalized_text(payload.get("input_type"))
        or normalized_text(getattr(interrupt, "input_type", None))
        or "text",
        choices=string_list(payload.get("choices"))
        or string_list(getattr(interrupt, "choices", None)),
    )


# MARK: Assignment and Enrichment


async def _forward_agent_assignment(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
) -> None:
    assignment = event.assignment
    agent_name = normalized_text(payload.get("agent_name")) or normalized_text(
        getattr(assignment, "agent_name", None)
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        getattr(assignment, "status", None)
    )
    if not agent_name or not status:
        return

    await bridge.emit_agent_assignment(
        agent_name=agent_name,
        status=status,
        assignment_id=normalized_text(payload.get("assignment_id"))
        or normalized_text(getattr(assignment, "id", None)),
        swarm_name=normalized_text(payload.get("swarm_name"))
        or normalized_text(getattr(assignment, "swarm_name", None)),
        task=normalized_text(payload.get("task"))
        or normalized_text(getattr(assignment, "task", None)),
        error=normalized_text(payload.get("error"))
        or normalized_text(getattr(assignment, "error", None)),
        result=payload.get("result", getattr(assignment, "result", None)),
    )


async def _forward_enrichment(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
) -> None:
    enrichment = event.enrichment
    phase = normalized_text(payload.get("phase")) or normalized_text(
        getattr(enrichment, "phase", None)
    )
    message = normalized_text(payload.get("message")) or normalized_text(
        getattr(enrichment, "message", None)
    )
    if not phase:
        return

    await bridge.emit_enrichment(
        phase=phase,
        message=message or phase,
        scope_id=normalized_text(payload.get("scope_id"))
        or normalized_text(getattr(event.scope, "id", None)),
        scope_name=normalized_text(payload.get("scope_name"))
        or normalized_text(getattr(event.scope, "name", None)),
        scope_type=normalized_text(payload.get("scope_type"))
        or normalized_text(getattr(event.scope, "type", None)),
        memory=coerce_mapping(payload.get("memory"))
        or coerce_mapping(getattr(enrichment, "memory", None)),
        redaction=coerce_mapping(payload.get("redaction"))
        or coerce_mapping(getattr(enrichment, "redaction", None)),
    )


# MARK: Tool Events


async def _forward_tool_event(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    tool = extract_tool_payload(event, payload=payload)
    if not tool.tool_id or not tool.tool_name or not tool.status:
        return

    normalized_status = normalize_tool_status(tool.status)
    normalized_type = normalize_tool_type(tool.tool_type)
    remember_tool_context(
        state,
        tool_id=tool.tool_id,
        tool_name=tool.tool_name,
        tool_type=normalized_type,
        agent_name=tool.agent_name,
        swarm_name=tool.swarm_name,
    )

    if normalized_type == "system":
        await _emit_system_tool_event(bridge, tool, normalized_status)
        if normalized_status in {"completed", "failed"}:
            clear_tool_state(state, tool.tool_id)
        return

    if normalized_type == "model":
        if normalized_status in {"completed", "failed"}:
            await bridge.emit_tool_event(
                tool_name=tool.tool_name,
                tool_id=tool.tool_id,
                status=normalized_status,
                args=tool.args,
                result=tool.result,
                error=tool.error,
                agent_name=tool.agent_name,
                swarm_name=tool.swarm_name,
                tool_type="model",
            )
            clear_tool_state(state, tool.tool_id)
        return

    await bridge.emit_tool_event(
        tool_name=tool.tool_name,
        tool_id=tool.tool_id,
        status=normalized_status,
        args=tool.args,
        result=tool.result,
        error=tool.error,
        agent_name=tool.agent_name,
        swarm_name=tool.swarm_name,
        tool_type=normalized_type,
    )
    if normalized_status in {"completed", "failed"}:
        clear_tool_state(state, tool.tool_id)


async def _forward_system_tool_start(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
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
    await bridge.emit_system_tool_start(
        tool_type=tool.tool_name,
        tool_id=tool.tool_id,
        params=tool.args,
        agent_name=tool.agent_name,
        swarm_name=tool.swarm_name,
    )


async def _forward_system_tool_chunk(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
) -> None:
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        getattr(event.tool, "id", None)
    )
    text = string_value(payload.get("text")) or string_value(getattr(event.chunk, "text", None))
    if tool_id and text is not None:
        progress = float_value(payload.get("progress"))
        if progress is None:
            progress = float_value(getattr(event.chunk, "progress", None))
        await bridge.emit_system_tool_chunk(
            tool_id=tool_id,
            text=text,
            progress=progress,
        )


async def _forward_system_tool_complete(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        getattr(event.tool, "id", None)
    )
    if not tool_id:
        return

    await bridge.emit_system_tool_complete(
        tool_id=tool_id,
        result=payload.get("result", getattr(event.tool, "result", None)),
    )
    clear_tool_state(state, tool_id)


async def _forward_system_tool_error(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        getattr(event.tool, "id", None)
    )
    context = state.tool_context_by_id.get(tool_id or "")
    tool_name = (
        context.name
        if context is not None
        else (normalized_text(payload.get("tool_name")) or "system_tool")
    )
    if not tool_id:
        return

    await bridge.emit_tool_event(
        tool_name=tool_name,
        tool_id=tool_id,
        status="failed",
        error=normalized_text(payload.get("error"))
        or normalized_text(getattr(event.tool, "error", None)),
        tool_type="system",
        agent_name=context.agent_name if context is not None else None,
        swarm_name=context.swarm_name if context is not None else None,
    )
    clear_tool_state(state, tool_id)


async def _emit_system_tool_event(bridge: EventBridge, tool: Any, status: str) -> None:
    if status == "executing":
        await bridge.emit_system_tool_start(
            tool_type=tool.tool_name,
            tool_id=tool.tool_id,
            params=tool.args,
            agent_name=tool.agent_name,
            swarm_name=tool.swarm_name,
        )
        return
    if status == "completed":
        await bridge.emit_system_tool_complete(tool_id=tool.tool_id, result=tool.result)
        return
    if status == "failed":
        await bridge.emit_tool_event(
            tool_name=tool.tool_name,
            tool_id=tool.tool_id,
            status="failed",
            args=tool.args,
            result=tool.result,
            error=tool.error,
            agent_name=tool.agent_name,
            swarm_name=tool.swarm_name,
            tool_type="system",
        )


__all__ = ["forward_to_bridge"]
