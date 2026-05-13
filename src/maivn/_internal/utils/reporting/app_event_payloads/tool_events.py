"""Payload builders for tool-execution and system-tool events.

``build_tool_event_payload`` covers the unified tool lifecycle (start /
complete / error) for func/agent/model/MCP tools. The ``build_system_tool_*``
builders carry the separate lifecycle used by streaming system tools where
``progress`` chunks arrive between ``start`` and ``complete``.
"""

from __future__ import annotations

from typing import Any

from .common import attach_common_fields, build_participant, build_scope, clean_text

# MARK: Tool Payloads


def build_tool_event_payload(
    *,
    tool_name: str,
    tool_id: str,
    status: str,
    args: Any = None,
    result: Any = None,
    error: str | None = None,
    agent_name: str | None = None,
    swarm_name: str | None = None,
    tool_type: str | None = None,
    participant_key: str | None = None,
    participant_name: str | None = None,
    participant_role: str | None = None,
) -> dict[str, Any]:
    """Build the unified tool-lifecycle payload.

    Args:
        tool_name: Display name of the tool.
        tool_id: Stable per-invocation identifier (event correlation key).
        status: ``"executing"`` / ``"completed"`` / ``"failed"``.
        args: Input arguments dict (may include ``arg_keys`` for redaction).
        result: Output value when ``status == "completed"``.
        error: Error message when ``status == "failed"``.
        agent_name / swarm_name: Scope context for the reporter.
        tool_type: ``"func"`` (default), ``"agent"``, ``"model"``,
            ``"system"``, or ``"mcp"``.
        participant_*: Optional participant metadata.
    """
    resolved_tool_type = clean_text(tool_type) or "func"
    resolved_args = dict(args) if isinstance(args, dict) else {}
    scope = build_scope(agent_name=agent_name, swarm_name=swarm_name)
    participant = build_participant(
        participant_key=participant_key,
        participant_name=participant_name,
        participant_role=participant_role,
    )
    payload = {
        "tool_name": tool_name,
        "tool_id": tool_id,
        "tool_type": resolved_tool_type,
        "status": status,
        "args": resolved_args,
        "result": result,
        "error": error,
        "agent_name": clean_text(agent_name),
        "swarm_name": clean_text(swarm_name),
        "participant_key": participant.get("key") if participant else None,
        "participant_name": participant.get("name") if participant else None,
        "participant_role": participant.get("role") if participant else None,
        "tool": {
            "id": tool_id,
            "name": tool_name,
            "type": resolved_tool_type,
            "status": status,
            "args": resolved_args,
            "result": result,
            "error": error,
        },
        "lifecycle": {"phase": status},
    }
    return attach_common_fields(
        payload,
        event_name="tool_event",
        event_kind="tool",
        scope=scope,
        participant=participant,
    )


# MARK: System Tool Payloads


def build_system_tool_start_payload(
    *,
    tool_type: str,
    tool_id: str,
    params: dict[str, Any] | None = None,
    agent_name: str | None = None,
    swarm_name: str | None = None,
) -> dict[str, Any]:
    """Build the opening event for a streaming system tool.

    System tools (think/search/etc.) emit ``start`` once, zero or more
    ``chunk`` events, then ``complete`` (or ``error``). ``tool_type`` acts as
    the tool name in this lifecycle since system tools don't carry a separate
    display name.
    """
    resolved_params = dict(params) if isinstance(params, dict) else {}
    scope = build_scope(agent_name=agent_name, swarm_name=swarm_name)
    payload = {
        "tool_type": tool_type,
        "tool_id": tool_id,
        "params": resolved_params,
        "agent_name": clean_text(agent_name),
        "swarm_name": clean_text(swarm_name),
        "tool": {
            "id": tool_id,
            "name": tool_type,
            "type": "system",
            "status": "executing",
            "args": resolved_params,
        },
        "lifecycle": {"phase": "executing"},
    }
    return attach_common_fields(
        payload,
        event_name="system_tool_start",
        event_kind="tool",
        scope=scope,
        participant=None,
    )


def build_system_tool_chunk_payload(
    *,
    tool_id: str,
    text: str,
    progress: float | None = None,
) -> dict[str, Any]:
    """Build an interim progress chunk for an in-flight system tool.

    ``progress`` is a 0..1 fraction when known; ``None`` for indeterminate.
    """
    payload = {
        "tool_id": tool_id,
        "text": text,
        "progress": progress,
        "tool": {
            "id": tool_id,
            "type": "system",
        },
        "chunk": {
            "text": text,
            "progress": progress,
        },
    }
    return attach_common_fields(
        payload,
        event_name="system_tool_chunk",
        event_kind="tool",
        scope=None,
        participant=None,
    )


def build_system_tool_complete_payload(*, tool_id: str, result: Any) -> dict[str, Any]:
    """Build the terminal ``complete`` event for a streaming system tool."""
    payload = {
        "tool_id": tool_id,
        "result": result,
        "tool": {
            "id": tool_id,
            "type": "system",
            "status": "completed",
            "result": result,
        },
        "lifecycle": {"phase": "completed"},
    }
    return attach_common_fields(
        payload,
        event_name="system_tool_complete",
        event_kind="tool",
        scope=None,
        participant=None,
    )
