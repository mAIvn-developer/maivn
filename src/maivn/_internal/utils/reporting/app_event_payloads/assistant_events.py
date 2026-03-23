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
