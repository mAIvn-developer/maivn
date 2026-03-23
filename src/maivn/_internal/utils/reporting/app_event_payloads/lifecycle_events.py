from __future__ import annotations

from typing import Any

from .common import attach_common_fields, build_participant, build_scope

# MARK: Enrichment Payloads


def build_enrichment_payload(
    *,
    phase: str,
    message: str,
    scope_id: str | None = None,
    scope_name: str | None = None,
    scope_type: str | None = None,
    memory: dict[str, Any] | None = None,
    redaction: dict[str, Any] | None = None,
    participant_key: str | None = None,
    participant_name: str | None = None,
    participant_role: str | None = None,
) -> dict[str, Any]:
    scope = build_scope(scope_id=scope_id, scope_name=scope_name, scope_type=scope_type)
    participant = build_participant(
        participant_key=participant_key,
        participant_name=participant_name,
        participant_role=participant_role,
    )
    normalized_memory = dict(memory) if isinstance(memory, dict) and memory else None
    normalized_redaction = dict(redaction) if isinstance(redaction, dict) and redaction else None
    payload = {
        "phase": phase,
        "message": message,
        "enrichment": {
            "phase": phase,
            "message": message,
        },
    }
    if scope is not None:
        if "id" in scope:
            payload["scope_id"] = scope["id"]
        if "name" in scope:
            payload["scope_name"] = scope["name"]
        if "type" in scope:
            payload["scope_type"] = scope["type"]
    if normalized_memory is not None:
        payload["memory"] = normalized_memory
        payload["enrichment"]["memory"] = normalized_memory
    if normalized_redaction is not None:
        payload["redaction"] = normalized_redaction
        payload["enrichment"]["redaction"] = normalized_redaction
    if participant is not None:
        if "key" in participant:
            payload["participant_key"] = participant["key"]
        if "name" in participant:
            payload["participant_name"] = participant["name"]
        if "role" in participant:
            payload["participant_role"] = participant["role"]
    return attach_common_fields(
        payload,
        event_name="enrichment",
        event_kind="enrichment",
        scope=scope,
        participant=participant,
    )


# MARK: Terminal and Session Payloads


def build_final_payload(
    *,
    response: str,
    result: Any = None,
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    responses = [response] if isinstance(response, str) and response.strip() else []
    payload = {
        "responses": responses,
        "response": response,
        "result": result,
        "token_usage": token_usage,
        "output": {
            "response": response,
            "result": result,
            "token_usage": token_usage,
        },
    }
    return attach_common_fields(
        payload,
        event_name="final",
        event_kind="final",
        scope=None,
        participant=None,
    )


def build_error_payload(*, error: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "error": error,
        "details": details or {},
        "error_info": {
            "message": error,
            "details": details or {},
        },
    }
    return attach_common_fields(
        payload,
        event_name="error",
        event_kind="error",
        scope=None,
        participant=None,
    )


def build_session_start_payload(*, session_id: str, assistant_id: str) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "assistant_id": assistant_id,
        "session": {
            "id": session_id,
            "assistant_id": assistant_id,
        },
    }
    return attach_common_fields(
        payload,
        event_name="session_start",
        event_kind="session",
        scope={"type": "session", "id": session_id},
        participant=None,
    )
