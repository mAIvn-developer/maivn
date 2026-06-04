"""Forwarders for tool execution and system tool events."""

# pyright: strict
from __future__ import annotations

from typing import Protocol, cast

from ..._models import AppEvent
from ..payload import (
    EventPayload,
    ToolArguments,
    ToolPayload,
    extract_tool_payload,
    normalize_tool_status,
    normalize_tool_type,
    normalized_text,
    string_value,
)
from ..state import NormalizedEventForwardingState, clear_tool_state, remember_tool_context

# MARK: Reporter Protocol


class ToolEventReporter(Protocol):
    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: ToolArguments | None = None,
        swarm_name: str | None = None,
    ) -> None: ...

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: object | None = None,
    ) -> None: ...

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None: ...

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: object | None = None,
    ) -> None: ...

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None: ...


# MARK: Tool Forwarding


def forward_tool_event(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    tool_reporter = cast(ToolEventReporter, reporter)
    tool = extract_tool_payload(event, payload=payload)
    if not tool.tool_id or not tool.tool_name or not tool.status:
        return

    normalized_status = normalize_tool_status(tool.status)
    normalized_type = normalize_tool_type(tool.tool_type)
    if normalized_type == "system":
        _remember_tool_payload_context(state, tool=tool, tool_type=normalized_type)
        if normalized_status == "executing":
            tool_reporter.report_tool_start(
                tool.tool_name,
                tool.tool_id,
                normalized_type,
                tool.agent_name,
                tool.args,
                tool.swarm_name,
            )
            return
        if normalized_status == "completed":
            tool_reporter.report_tool_complete(tool.tool_id, result=tool.result)
            clear_tool_state(state, tool.tool_id)
            return
        if normalized_status == "failed":
            tool_reporter.report_tool_error(
                tool.tool_name, tool.error or "Unknown error", event_id=tool.tool_id
            )
            clear_tool_state(state, tool.tool_id)
            return
        return

    if normalized_type == "model":
        if normalized_status == "completed":
            tool_reporter.report_model_tool_complete(
                tool.tool_name,
                event_id=tool.tool_id,
                agent_name=tool.agent_name,
                swarm_name=tool.swarm_name,
                result=tool.result,
            )
            clear_tool_state(state, tool.tool_id)
            return
        if normalized_status == "failed":
            tool_reporter.report_tool_error(
                tool.tool_name, tool.error or "Unknown error", event_id=tool.tool_id
            )
            clear_tool_state(state, tool.tool_id)
            return
        _remember_tool_payload_context(state, tool=tool, tool_type=normalized_type)
        return

    _remember_tool_payload_context(state, tool=tool, tool_type=normalized_type)
    if normalized_status == "executing":
        tool_reporter.report_tool_start(
            tool.tool_name,
            tool.tool_id,
            normalized_type,
            tool.agent_name,
            tool.args,
            tool.swarm_name,
        )
        return
    if normalized_status == "completed":
        tool_reporter.report_tool_complete(tool.tool_id, result=tool.result)
        clear_tool_state(state, tool.tool_id)
        return
    if normalized_status == "failed":
        tool_reporter.report_tool_error(
            tool.tool_name, tool.error or "Unknown error", event_id=tool.tool_id
        )
        clear_tool_state(state, tool.tool_id)


# MARK: System Tool Forwarding


def forward_system_tool_start(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    tool_reporter = cast(ToolEventReporter, reporter)
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
    tool_reporter.report_tool_start(
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
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    tool_reporter = cast(ToolEventReporter, reporter)
    tool = event.tool
    chunk = event.chunk
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        tool.id if tool is not None else None
    )
    if not tool_id:
        return

    context = state.tool_context_by_id.get(tool_id)
    tool_name = context.name if context is not None else "system_tool"
    chunk_text = string_value(payload.get("text")) or string_value(
        chunk.text if chunk is not None else None
    )
    chunk_count = state.system_tool_chunk_count_by_id.get(tool_id, 0) + 1
    state.system_tool_chunk_count_by_id[tool_id] = chunk_count

    tool_reporter.report_system_tool_progress(
        event_id=tool_id,
        tool_name=tool_name,
        chunk_count=chunk_count,
        elapsed_seconds=0.0,
        text=chunk_text,
    )


def forward_system_tool_complete(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    tool_reporter = cast(ToolEventReporter, reporter)
    tool = event.tool
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        tool.id if tool is not None else None
    )
    if not tool_id:
        return

    tool_reporter.report_tool_complete(
        tool_id,
        result=payload.get("result", cast(object, tool.result) if tool is not None else None),
    )
    clear_tool_state(state, tool_id)


def forward_system_tool_error(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    tool_reporter = cast(ToolEventReporter, reporter)
    tool = event.tool
    tool_id = normalized_text(payload.get("tool_id")) or normalized_text(
        tool.id if tool is not None else None
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
        tool.error if tool is not None else None
    )
    tool_reporter.report_tool_error(tool_name, error or "Unknown error", event_id=tool_id)
    clear_tool_state(state, tool_id)


# MARK: Helpers


def _remember_tool_payload_context(
    state: NormalizedEventForwardingState,
    *,
    tool: ToolPayload,
    tool_type: str,
) -> None:
    if tool.tool_id is None or tool.tool_name is None:
        return
    remember_tool_context(
        state,
        tool_id=tool.tool_id,
        tool_name=tool.tool_name,
        tool_type=tool_type,
        agent_name=tool.agent_name,
        swarm_name=tool.swarm_name,
    )


__all__ = [
    "forward_system_tool_chunk",
    "forward_system_tool_complete",
    "forward_system_tool_error",
    "forward_system_tool_start",
    "forward_tool_event",
]
