from __future__ import annotations

from typing import Any

# MARK: Configuration


APP_EVENT_CONTRACT_VERSION = "v1"


# MARK: Shared Helpers


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def build_scope(
    *,
    scope_id: str | None = None,
    scope_name: str | None = None,
    scope_type: str | None = None,
    agent_name: str | None = None,
    swarm_name: str | None = None,
) -> dict[str, Any] | None:
    normalized_type = clean_text(scope_type)
    if normalized_type is not None:
        normalized_type = normalized_type.lower()
        if normalized_type not in {"agent", "swarm", "session"}:
            normalized_type = None

    normalized_id = clean_text(scope_id)
    normalized_name = clean_text(scope_name)

    if normalized_type is None:
        swarm_candidate = clean_text(swarm_name)
        agent_candidate = clean_text(agent_name)
        if swarm_candidate is not None:
            normalized_type = "swarm"
            normalized_name = normalized_name or swarm_candidate
        elif agent_candidate is not None:
            normalized_type = "agent"
            normalized_name = normalized_name or agent_candidate

    scope: dict[str, Any] = {}
    if normalized_type is not None:
        scope["type"] = normalized_type
    if normalized_id is not None:
        scope["id"] = normalized_id
    if normalized_name is not None:
        scope["name"] = normalized_name
    return scope or None


def build_participant(
    *,
    participant_key: str | None = None,
    participant_name: str | None = None,
    participant_role: str | None = None,
) -> dict[str, Any] | None:
    participant: dict[str, Any] = {}
    normalized_key = clean_text(participant_key)
    normalized_name = clean_text(participant_name)
    normalized_role = clean_text(participant_role)
    if normalized_key is not None:
        participant["key"] = normalized_key
    if normalized_name is not None:
        participant["name"] = normalized_name
    if normalized_role is not None:
        participant["role"] = normalized_role
    return participant or None


def attach_common_fields(
    payload: dict[str, Any],
    *,
    event_name: str,
    event_kind: str,
    scope: dict[str, Any] | None,
    participant: dict[str, Any] | None,
) -> dict[str, Any]:
    payload["contract_version"] = APP_EVENT_CONTRACT_VERSION
    payload["event_name"] = event_name
    payload["event_kind"] = event_kind
    if scope is not None:
        payload["scope"] = scope
    if participant is not None:
        payload["participant"] = participant
    return payload
