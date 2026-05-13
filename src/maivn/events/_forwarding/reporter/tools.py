"""Forwarders for tool execution and system tool events."""

from __future__ import annotations

from typing import Any

from ..._models import AppEvent
from ..payload import (
    extract_tool_payload,
    normalize_tool_status,
    normalize_tool_type,
    normalized_text,
    string_value,
)
from ..state import NormalizedEventForwardingState, clear_tool_state, remember_tool_context


def forward_tool_event(
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


def forward_system_tool_start(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = event
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


def forward_system_tool_chunk(
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


def forward_system_tool_complete(
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


def forward_system_tool_error(
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


__all__ = [
    "forward_system_tool_chunk",
    "forward_system_tool_complete",
    "forward_system_tool_error",
    "forward_system_tool_start",
    "forward_tool_event",
]
