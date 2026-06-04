"""Typed bridge emitters backed by canonical payload builders."""

# pyright: strict
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import cast

from pydantic import JsonValue

from ..._internal.utils.reporting.app_event_payloads import (
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_hook_fired_payload,
    build_interrupt_required_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
)

EventPayload = dict[str, object]
JsonObject = dict[str, JsonValue]
EmitFn = Callable[[str, EventPayload], Awaitable[None]]


# MARK: Tool Emitters


async def emit_tool_event(
    emit: EmitFn,
    *,
    tool_name: str,
    tool_id: str,
    status: str,
    args: EventPayload | None = None,
    result: object = None,
    error: str | None = None,
    agent_name: str | None = None,
    swarm_name: str | None = None,
    tool_type: str | None = None,
) -> None:
    await emit(
        "tool_event",
        cast(
            EventPayload,
            build_tool_event_payload(
                tool_name=tool_name,
                tool_id=tool_id,
                status=status,
                args=args,
                result=cast(JsonValue, result),
                error=error,
                agent_name=agent_name,
                swarm_name=swarm_name,
                tool_type=tool_type,
            ),
        ),
    )


async def emit_system_tool_start(
    emit: EmitFn,
    *,
    tool_type: str,
    tool_id: str,
    params: EventPayload | None = None,
    agent_name: str | None = None,
    swarm_name: str | None = None,
) -> None:
    await emit(
        "system_tool_start",
        cast(
            EventPayload,
            build_system_tool_start_payload(
                tool_type=tool_type,
                tool_id=tool_id,
                params=params,
                agent_name=agent_name,
                swarm_name=swarm_name,
            ),
        ),
    )


async def emit_system_tool_chunk(
    emit: EmitFn,
    *,
    tool_id: str,
    text: str,
    progress: float | None = None,
) -> None:
    await emit(
        "system_tool_chunk",
        cast(
            EventPayload,
            build_system_tool_chunk_payload(
                tool_id=tool_id,
                text=text,
                progress=progress,
            ),
        ),
    )


async def emit_system_tool_complete(
    emit: EmitFn,
    *,
    tool_id: str,
    result: object,
) -> None:
    await emit(
        "system_tool_complete",
        cast(
            EventPayload,
            build_system_tool_complete_payload(tool_id=tool_id, result=cast(JsonValue, result)),
        ),
    )


# MARK: Assistant and Lifecycle Emitters


async def emit_assistant_chunk(
    emit: EmitFn,
    *,
    assistant_id: str,
    text: str,
    replace_content: bool = False,
) -> None:
    await emit(
        "assistant_chunk",
        cast(
            EventPayload,
            build_assistant_chunk_payload(
                assistant_id=assistant_id,
                text=text,
                replace_content=replace_content,
            ),
        ),
    )


async def emit_status_message(
    emit: EmitFn,
    *,
    assistant_id: str,
    message: str,
) -> None:
    await emit(
        "status_message",
        cast(
            EventPayload,
            build_status_message_payload(assistant_id=assistant_id, message=message),
        ),
    )


async def emit_interrupt_required(
    emit: EmitFn,
    *,
    interrupt_id: str,
    data_key: str,
    prompt: str,
    arg_name: str | None = None,
    tool_name: str | None = None,
    checkpoint_id: str | None = None,
    assignment_id: str | None = None,
    interrupt_number: int = 1,
    total_interrupts: int = 1,
    input_type: str = "text",
    choices: list[str] | None = None,
) -> None:
    await emit(
        "interrupt_required",
        cast(
            EventPayload,
            build_interrupt_required_payload(
                interrupt_id=interrupt_id,
                checkpoint_id=checkpoint_id,
                data_key=data_key,
                prompt=prompt,
                tool_name=tool_name,
                arg_name=arg_name or data_key,
                assignment_id=assignment_id,
                interrupt_number=interrupt_number,
                total_interrupts=total_interrupts,
                input_type=input_type,
                choices=choices,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        ),
    )


async def emit_agent_assignment(
    emit: EmitFn,
    *,
    agent_name: str,
    status: str,
    assignment_id: str | None = None,
    swarm_name: str | None = None,
    task: str | None = None,
    error: str | None = None,
    result: object | None = None,
) -> None:
    await emit(
        "agent_assignment",
        cast(
            EventPayload,
            build_agent_assignment_payload(
                assignment_id=assignment_id,
                agent_name=agent_name,
                status=status,
                task=task,
                swarm_name=swarm_name,
                error=error,
                result=cast(JsonValue, result),
            ),
        ),
    )


async def emit_enrichment(
    emit: EmitFn,
    *,
    phase: str,
    message: str,
    scope_id: str | None = None,
    scope_name: str | None = None,
    scope_type: str | None = None,
    memory: EventPayload | None = None,
    redaction: EventPayload | None = None,
    reevaluate: EventPayload | None = None,
) -> None:
    await emit(
        "enrichment",
        cast(
            EventPayload,
            build_enrichment_payload(
                phase=phase,
                message=message,
                scope_id=scope_id,
                scope_name=scope_name,
                scope_type=scope_type,
                memory=cast(JsonObject | None, memory),
                redaction=cast(JsonObject | None, redaction),
                reevaluate=cast(JsonObject | None, reevaluate),
            ),
        ),
    )


async def emit_final(emit: EmitFn, *, response: str, result: object = None) -> None:
    await emit(
        "final",
        cast(
            EventPayload,
            build_final_payload(response=response, result=cast(JsonValue, result)),
        ),
    )


async def emit_error(
    emit: EmitFn,
    *,
    error: str,
    details: EventPayload | None = None,
) -> None:
    await emit(
        "error",
        cast(
            EventPayload,
            build_error_payload(error=error, details=cast(JsonObject | None, details)),
        ),
    )


# MARK: Hook Emitters


async def emit_hook_fired(
    emit: EmitFn,
    *,
    name: str,
    stage: str,
    status: str,
    target_type: str,
    target_id: str | None = None,
    target_name: str | None = None,
    source: str | None = None,
    error: str | None = None,
    elapsed_ms: int | None = None,
) -> None:
    """Emit a single developer-registered hook callback firing.

    See :func:`build_hook_fired_payload` for argument semantics.
    """
    await emit(
        "hook_fired",
        cast(
            EventPayload,
            build_hook_fired_payload(
                name=name,
                stage=stage,
                status=status,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                source=source,
                error=error,
                elapsed_ms=elapsed_ms,
            ),
        ),
    )
