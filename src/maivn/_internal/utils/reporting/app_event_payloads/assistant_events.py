"""Payload builders for assistant, status, interrupt, and assignment events.

Each ``build_*_payload`` produces a canonical AppEvent payload dict ready to
be wrapped in an :class:`~maivn.events.AppEvent` envelope. Builders attach the
shared ``contract_version`` / ``event_name`` / ``event_kind`` / ``scope`` /
``participant`` fields via :func:`attach_common_fields` so the resulting dicts
satisfy the v1 stream schema without callers reimplementing the envelope.
"""

from __future__ import annotations

from typing import Any

from .common import attach_common_fields, build_participant, build_scope, clean_text

# MARK: Assistant and Status Payloads


def build_assistant_chunk_payload(
    *,
    assistant_id: str,
    text: str,
    participant_key: str | None = None,
    participant_name: str | None = None,
    participant_role: str | None = None,
) -> dict[str, Any]:
    """Build the payload for an incremental assistant response chunk.

    Args:
        assistant_id: Stable identifier for the streaming assistant.
        text: Delta text to append; full text is reconstructed downstream
            by accumulating consecutive deltas keyed by ``assistant_id``.
        participant_key/name/role: Optional participant metadata for
            multi-participant streams (e.g. swarm members).
    """
    participant = build_participant(
        participant_key=participant_key,
        participant_name=participant_name,
        participant_role=participant_role,
    )
    payload = {
        "assistant_id": assistant_id,
        "text": text,
        "participant_key": participant.get("key") if participant else None,
        "participant_name": participant.get("name") if participant else None,
        "participant_role": participant.get("role") if participant else None,
        "assistant": {
            "id": assistant_id,
            "delta": text,
        },
    }
    return attach_common_fields(
        payload,
        event_name="assistant_chunk",
        event_kind="assistant",
        scope=None,
        participant=participant,
    )


def build_status_message_payload(*, assistant_id: str, message: str) -> dict[str, Any]:
    """Build the payload for a standalone, non-streaming status line.

    Status messages are short user-facing strings (e.g. "Searching tools…")
    surfaced between deltas; downstream reporters typically render them as a
    single line rather than appending to streamed text.
    """
    payload = {
        "assistant_id": assistant_id,
        "message": message,
        "assistant": {"id": assistant_id},
        "status": {"message": message},
    }
    return attach_common_fields(
        payload,
        event_name="status_message",
        event_kind="status",
        scope=None,
        participant=None,
    )


# MARK: Interrupt and Assignment Payloads


def build_interrupt_required_payload(
    *,
    interrupt_id: str,
    data_key: str,
    prompt: str,
    tool_name: str | None = None,
    arg_name: str | None = None,
    checkpoint_id: str | None = None,
    assignment_id: str | None = None,
    interrupt_number: int | None = None,
    total_interrupts: int | None = None,
    input_type: str | None = None,
    choices: list[str] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build the payload announcing an execution-pause user-input request.

    Args:
        interrupt_id: Unique id for this interrupt instance.
        data_key: Identifier the runtime uses to route the answer back into
            the agent's private data / tool args.
        prompt: Human-readable prompt to display.
        tool_name / arg_name: Which tool argument this answer satisfies.
        checkpoint_id / assignment_id: Surrounding execution context, when
            available.
        interrupt_number / total_interrupts: Position in a batched series.
        input_type: ``"text"`` / ``"choice"`` / ``"boolean"`` / etc.
        choices: Allowed options when ``input_type == "choice"``.
        timestamp: ISO-8601 emission time, if the caller wants to override.
    """
    normalized_choices = [str(choice) for choice in choices or []]
    payload = {
        "interrupt_id": interrupt_id,
        "checkpoint_id": checkpoint_id or "",
        "data_key": data_key,
        "prompt": prompt,
        "tool_name": tool_name or "",
        "arg_name": arg_name or data_key,
        "assignment_id": assignment_id or "",
        "assignment_index": 0,
        "interrupt_number": interrupt_number or 1,
        "total_interrupts": total_interrupts or 1,
        "input_type": input_type or "text",
        "choices": normalized_choices,
        "timestamp": timestamp,
        "interrupt": {
            "id": interrupt_id,
            "checkpoint_id": checkpoint_id or None,
            "assignment_id": assignment_id or None,
            "data_key": data_key,
            "arg_name": arg_name or data_key,
            "prompt": prompt,
            "tool_name": tool_name or None,
            "input_type": input_type or "text",
            "choices": normalized_choices,
            "number": interrupt_number or 1,
            "total": total_interrupts or 1,
        },
    }
    return attach_common_fields(
        payload,
        event_name="interrupt_required",
        event_kind="interrupt",
        scope=None,
        participant=None,
    )


def build_agent_assignment_payload(
    *,
    agent_name: str,
    status: str,
    assignment_id: str | None = None,
    swarm_name: str | None = None,
    task: str | None = None,
    error: str | None = None,
    result: Any = None,
    participant_key: str | None = None,
    participant_name: str | None = None,
    participant_role: str | None = None,
) -> dict[str, Any]:
    """Build the payload for a per-agent lifecycle update inside a swarm.

    Emitted whenever an agent transitions through ``in_progress`` /
    ``completed`` / ``failed`` so frontend UIs can render per-agent cards.
    ``error`` and ``result`` are mutually exclusive but neither is required.
    """
    scope = build_scope(
        scope_type="swarm" if clean_text(swarm_name) else None,
        scope_name=swarm_name,
    )
    participant = build_participant(
        participant_key=participant_key,
        participant_name=participant_name,
        participant_role=participant_role,
    )
    payload = {
        "assignment_id": assignment_id,
        "agent_name": agent_name,
        "status": status,
        "task": task,
        "swarm_name": clean_text(swarm_name),
        "result": result,
        "error": error,
        "participant_key": participant.get("key") if participant else None,
        "participant_name": participant.get("name") if participant else None,
        "participant_role": participant.get("role") if participant else None,
        "assignment": {
            "id": assignment_id,
            "agent_name": agent_name,
            "status": status,
            "task": task,
            "swarm_name": clean_text(swarm_name),
            "result": result,
            "error": error,
        },
        "lifecycle": {"phase": status},
    }
    return attach_common_fields(
        payload,
        event_name="agent_assignment",
        event_kind="assignment",
        scope=scope,
        participant=participant,
    )
