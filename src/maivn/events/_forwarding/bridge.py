"""Forward normalized AppEvents into UI event bridges."""

# pyright: strict
from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, cast

from .._bridge import EventBridge
from .._models import AppEvent
from .payload import (
    EventPayload,
    ToolPayload,
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

# MARK: Types


class BridgeForwarder(Protocol):
    def __call__(
        self,
        event: AppEvent,
        *,
        payload: EventPayload,
        bridge: EventBridge,
        state: NormalizedEventForwardingState,
    ) -> Awaitable[None]: ...


# MARK: Configuration


_TERMINAL_TOOL_STATUSES: frozenset[str] = frozenset({"completed", "failed"})


# MARK: Dispatcher


async def forward_to_bridge(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    event_name = normalized_text(event.event_name) or ""
    forwarder = _BRIDGE_DISPATCHERS.get(event_name)
    if forwarder is not None:
        await forwarder(event, payload=payload, bridge=bridge, state=state)
        return

    await bridge.emit(event_name, payload)


# MARK: Assistant Events


async def _forward_assistant_chunk(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    assistant = event.assistant
    text = string_value(payload.get("text")) or string_value(
        assistant.delta if assistant is not None else None
    )
    if not text:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        assistant.id if assistant is not None else None
    )
    # Honor the normalize-layer ``replace_content`` signal so the wire
    # carries it through to the UI. Without this, a fresh-stream chunk
    # (cycle 2 after a reevaluate) reaches the frontend as a normal
    # append-chunk and the UI keeps concatenating onto the prior cycle.
    replace_content = bool(payload.get("replace_content")) or bool(
        assistant.replace_content if assistant is not None else False
    )
    await bridge.emit_assistant_chunk(
        assistant_id=assistant_id or "assistant",
        text=text,
        replace_content=replace_content,
    )


async def _forward_status_message(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    message = string_value(payload.get("message")) or string_value(
        mapping_value(payload.get("status"), "message")
    )
    if not message:
        return

    assistant_id = normalized_text(payload.get("assistant_id")) or normalized_text(
        event.assistant.id if event.assistant is not None else None
    )
    await bridge.emit_status_message(
        assistant_id=assistant_id or "assistant",
        message=message,
    )


# MARK: Interrupts


async def _forward_interrupt_required(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    interrupt = event.interrupt
    interrupt_id = normalized_text(payload.get("interrupt_id")) or normalized_text(
        interrupt.id if interrupt is not None else None
    )
    data_key = normalized_text(payload.get("data_key")) or normalized_text(
        interrupt.data_key if interrupt is not None else None
    )
    prompt = string_value(payload.get("prompt")) or string_value(
        interrupt.prompt if interrupt is not None else None
    )
    if not interrupt_id or not data_key or not prompt:
        return

    await bridge.emit_interrupt_required(
        interrupt_id=interrupt_id,
        checkpoint_id=normalized_text(payload.get("checkpoint_id"))
        or normalized_text(interrupt.checkpoint_id if interrupt is not None else None),
        data_key=data_key,
        prompt=prompt,
        tool_name=normalized_text(payload.get("tool_name"))
        or normalized_text(interrupt.tool_name if interrupt is not None else None),
        arg_name=normalized_text(payload.get("arg_name"))
        or normalized_text(interrupt.arg_name if interrupt is not None else None),
        assignment_id=normalized_text(payload.get("assignment_id"))
        or normalized_text(interrupt.assignment_id if interrupt is not None else None),
        interrupt_number=integer_value(payload.get("interrupt_number"))
        or integer_value(interrupt.number if interrupt is not None else None)
        or 1,
        total_interrupts=integer_value(payload.get("total_interrupts"))
        or integer_value(interrupt.total if interrupt is not None else None)
        or 1,
        input_type=normalized_text(payload.get("input_type"))
        or normalized_text(interrupt.input_type if interrupt is not None else None)
        or "text",
        choices=string_list(payload.get("choices"))
        or string_list(interrupt.choices if interrupt is not None else None),
    )


# MARK: Assignment and Enrichment


async def _forward_agent_assignment(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    assignment = event.assignment
    agent_name = normalized_text(payload.get("agent_name")) or normalized_text(
        assignment.agent_name if assignment is not None else None
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        assignment.status if assignment is not None else None
    )
    if not agent_name or not status:
        return

    await bridge.emit_agent_assignment(
        agent_name=agent_name,
        status=status,
        assignment_id=normalized_text(payload.get("assignment_id"))
        or normalized_text(assignment.id if assignment is not None else None),
        swarm_name=normalized_text(payload.get("swarm_name"))
        or normalized_text(assignment.swarm_name if assignment is not None else None),
        task=normalized_text(payload.get("task"))
        or normalized_text(assignment.task if assignment is not None else None),
        error=normalized_text(payload.get("error"))
        or normalized_text(assignment.error if assignment is not None else None),
        result=payload.get(
            "result",
            cast(object, assignment.result) if assignment is not None else None,
        ),
    )


async def _forward_enrichment(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    enrichment = event.enrichment
    scope = event.scope
    phase = normalized_text(payload.get("phase")) or normalized_text(
        enrichment.phase if enrichment is not None else None
    )
    message = normalized_text(payload.get("message")) or normalized_text(
        enrichment.message if enrichment is not None else None
    )
    if not phase:
        return

    await bridge.emit_enrichment(
        phase=phase,
        message=message or phase,
        scope_id=normalized_text(payload.get("scope_id"))
        or normalized_text(scope.id if scope is not None else None),
        scope_name=normalized_text(payload.get("scope_name"))
        or normalized_text(scope.name if scope is not None else None),
        scope_type=normalized_text(payload.get("scope_type"))
        or normalized_text(scope.type if scope is not None else None),
        memory=coerce_mapping(payload.get("memory"))
        or coerce_mapping(cast(object, enrichment.memory) if enrichment is not None else None),
        redaction=coerce_mapping(payload.get("redaction"))
        or coerce_mapping(cast(object, enrichment.redaction) if enrichment is not None else None),
    )


# MARK: Tool Events


async def _forward_tool_event(
    event: AppEvent,
    *,
    payload: EventPayload,
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
        if normalized_status in _TERMINAL_TOOL_STATUSES:
            clear_tool_state(state, tool.tool_id)
        return

    if normalized_type == "model":
        if normalized_status in _TERMINAL_TOOL_STATUSES:
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
    if normalized_status in _TERMINAL_TOOL_STATUSES:
        clear_tool_state(state, tool.tool_id)


async def _forward_system_tool_start(
    event: AppEvent,
    *,
    payload: EventPayload,
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
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    tool = event.tool
    chunk = event.chunk
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        tool.id if tool is not None else None
    )
    text = string_value(payload.get("text")) or string_value(
        chunk.text if chunk is not None else None
    )
    if tool_id and text is not None:
        progress = float_value(payload.get("progress"))
        if progress is None:
            progress = float_value(chunk.progress if chunk is not None else None)
        await bridge.emit_system_tool_chunk(
            tool_id=tool_id,
            text=text,
            progress=progress,
        )


async def _forward_system_tool_complete(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    tool = event.tool
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        tool.id if tool is not None else None
    )
    if not tool_id:
        return

    await bridge.emit_system_tool_complete(
        tool_id=tool_id,
        result=payload.get("result", cast(object, tool.result) if tool is not None else None),
    )
    clear_tool_state(state, tool_id)


async def _forward_system_tool_error(
    event: AppEvent,
    *,
    payload: EventPayload,
    bridge: EventBridge,
    state: NormalizedEventForwardingState,
) -> None:
    tool = event.tool
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        tool.id if tool is not None else None
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
        or normalized_text(tool.error if tool is not None else None),
        tool_type="system",
        agent_name=context.agent_name if context is not None else None,
        swarm_name=context.swarm_name if context is not None else None,
    )
    clear_tool_state(state, tool_id)


async def _emit_system_tool_event(bridge: EventBridge, tool: ToolPayload, status: str) -> None:
    if tool.tool_id is None or tool.tool_name is None:
        return

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


_BRIDGE_DISPATCHERS: dict[str, BridgeForwarder] = {
    "assistant_chunk": _forward_assistant_chunk,
    "status_message": _forward_status_message,
    "interrupt_required": _forward_interrupt_required,
    "agent_assignment": _forward_agent_assignment,
    "enrichment": _forward_enrichment,
    "tool_event": _forward_tool_event,
    "system_tool_start": _forward_system_tool_start,
    "system_tool_chunk": _forward_system_tool_chunk,
    "system_tool_complete": _forward_system_tool_complete,
    "system_tool_error": _forward_system_tool_error,
}


__all__ = ["forward_to_bridge"]
