# pyright: strict
"""Payload builders for enrichment, terminal, and session-lifecycle events."""

from __future__ import annotations

from pydantic import JsonValue

from .common import (
    JsonObject,
    attach_common_fields,
    build_participant,
    build_scope,
    copy_json_object,
)

# MARK: Enrichment Payloads


def build_enrichment_payload(
    *,
    phase: str,
    message: str,
    scope_id: str | None = None,
    scope_name: str | None = None,
    scope_type: str | None = None,
    memory: JsonObject | None = None,
    redaction: JsonObject | None = None,
    reevaluate: JsonObject | None = None,
    participant_key: str | None = None,
    participant_name: str | None = None,
    participant_role: str | None = None,
) -> JsonObject:
    """Build the payload for an enrichment-phase milestone.

    Enrichment events bracket the work the runtime performs around an
    invocation (memory retrieval, redaction, etc.). ``memory``,
    ``redaction``, and ``reevaluate`` carry the per-phase metric dicts when
    emitted by the corresponding subsystems; reporters render these as
    inline metrics. ``reevaluate`` is specifically for
    ``phase="reevaluate_accrued"`` events and carries source/trigger/target
    attribution plus cycle and collected counts.
    """
    scope = build_scope(scope_id=scope_id, scope_name=scope_name, scope_type=scope_type)
    participant = build_participant(
        participant_key=participant_key,
        participant_name=participant_name,
        participant_role=participant_role,
    )
    # Empty enrichment dicts ({}) and None are both elided from the payload —
    # callers signal "no data for this phase" by passing an empty dict (idiomatic
    # for optional metric-bags) or by omitting the kwarg entirely. Only non-empty
    # dicts produce ``payload["memory"]`` / ``payload["redaction"]`` /
    # ``payload["reevaluate"]`` keys. Fixes the
    # ``test_emit_enrichment_ignores_empty_dicts`` contract in maivn-studio.
    normalized_memory = copy_json_object(memory) if memory else None
    normalized_redaction = copy_json_object(redaction) if redaction else None
    normalized_reevaluate = copy_json_object(reevaluate) if reevaluate else None
    enrichment: JsonObject = {
        "phase": phase,
        "message": message,
    }
    payload: JsonObject = {
        "phase": phase,
        "message": message,
        "enrichment": enrichment,
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
        enrichment["memory"] = normalized_memory
    if normalized_redaction is not None:
        payload["redaction"] = normalized_redaction
        enrichment["redaction"] = normalized_redaction
    if normalized_reevaluate is not None:
        payload["reevaluate"] = normalized_reevaluate
        enrichment["reevaluate"] = normalized_reevaluate
        # Flat-field projection so consumers that key on individual fields
        # (e.g. third-party event hooks) still see them.
        for key in ("source", "trigger_tool", "target_tool"):
            value = normalized_reevaluate.get(key)
            if isinstance(value, str) and value:
                payload[key] = value
        for key in ("reevaluate_count", "collected_count"):
            value = normalized_reevaluate.get(key)
            if isinstance(value, int):
                payload[key] = value
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
    result: JsonValue = None,
    token_usage: JsonObject | None = None,
) -> JsonObject:
    """Build the terminal ``final`` payload that closes a successful run.

    Carries the final assistant response text, the structured result (when
    a final-tool was used), and a token-usage summary for the whole
    invocation.
    """
    responses: list[JsonValue] = [response] if response.strip() else []
    output: JsonObject = {
        "response": response,
        "result": result,
        "token_usage": token_usage,
    }
    payload: JsonObject = {
        "responses": responses,
        "response": response,
        "result": result,
        "token_usage": token_usage,
        "output": output,
    }
    return attach_common_fields(
        payload,
        event_name="final",
        event_kind="final",
        scope=None,
        participant=None,
    )


def build_error_payload(*, error: str, details: JsonObject | None = None) -> JsonObject:
    """Build the terminal ``error`` payload for a failed invocation.

    ``error`` is the user-facing message; ``details`` may carry structured
    diagnostic context that frontends choose whether to expose.
    """
    resolved_details: JsonObject = details or {}
    error_info: JsonObject = {
        "message": error,
        "details": resolved_details,
    }
    payload: JsonObject = {
        "error": error,
        "details": resolved_details,
        "error_info": error_info,
    }
    return attach_common_fields(
        payload,
        event_name="error",
        event_kind="error",
        scope=None,
        participant=None,
    )


def build_session_start_payload(*, session_id: str, assistant_id: str) -> JsonObject:
    """Build the ``session_start`` payload that opens a run.

    Reporters / bridges use the ``(session_id, assistant_id)`` pair to anchor
    all subsequent events to the right UI surface.
    """
    session: JsonObject = {
        "id": session_id,
        "assistant_id": assistant_id,
    }
    scope: JsonObject = {"type": "session", "id": session_id}
    payload: JsonObject = {
        "session_id": session_id,
        "assistant_id": assistant_id,
        "session": session,
    }
    return attach_common_fields(
        payload,
        event_name="session_start",
        event_kind="session",
        scope=scope,
        participant=None,
    )
