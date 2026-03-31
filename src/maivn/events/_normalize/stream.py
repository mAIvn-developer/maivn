"""Stream normalization entry points for AppEvent payloads."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from ..._internal.utils.reporting.app_event_payloads import APP_EVENT_CONTRACT_VERSION
from .._models import AppEvent, NormalizedStreamState, RawSSEEvent
from .context import NormalizationOptions
from .handlers import EVENT_HANDLERS
from .helpers import clean_text, coerce_mapping, validate_payload


def normalize_stream_event(
    event: RawSSEEvent,
    *,
    state: NormalizedStreamState | None = None,
    default_agent_name: str | None = None,
    default_swarm_name: str | None = None,
    default_participant_key: str | None = None,
    default_participant_name: str | None = None,
    default_participant_role: str | None = None,
    assignment_name_map: dict[str, str] | None = None,
    tool_name_map: dict[str, str] | None = None,
    tool_metadata_map: dict[str, dict[str, Any]] | None = None,
) -> list[AppEvent]:
    active_state = state or NormalizedStreamState()
    payload = coerce_mapping(getattr(event, "payload", {}))
    name = clean_text(getattr(event, "name", "")) or ""

    if payload.get("contract_version") == APP_EVENT_CONTRACT_VERSION and payload.get("event_name"):
        return [validate_payload(payload)]

    options = NormalizationOptions(
        default_agent_name=default_agent_name,
        default_swarm_name=default_swarm_name,
        default_participant_key=default_participant_key,
        default_participant_name=default_participant_name,
        default_participant_role=default_participant_role,
        assignment_name_map=assignment_name_map,
        tool_name_map=tool_name_map,
        tool_metadata_map=tool_metadata_map,
    )
    handler = EVENT_HANDLERS.get(name)
    if handler is None:
        return []

    normalized_payloads = handler(payload, active_state, options)
    return [validate_payload(item) for item in normalized_payloads]


def normalize_stream(
    events: Iterable[RawSSEEvent],
    *,
    default_agent_name: str | None = None,
    default_swarm_name: str | None = None,
    default_participant_key: str | None = None,
    default_participant_name: str | None = None,
    default_participant_role: str | None = None,
    assignment_name_map: dict[str, str] | None = None,
    tool_name_map: dict[str, str] | None = None,
    tool_metadata_map: dict[str, dict[str, Any]] | None = None,
) -> Iterator[AppEvent]:
    state = NormalizedStreamState()
    stream_options: dict[str, Any] = {
        "default_agent_name": default_agent_name,
        "default_swarm_name": default_swarm_name,
        "default_participant_key": default_participant_key,
        "default_participant_name": default_participant_name,
        "default_participant_role": default_participant_role,
        "assignment_name_map": assignment_name_map,
        "tool_name_map": tool_name_map,
        "tool_metadata_map": tool_metadata_map,
    }
    for event in events:
        yield from normalize_stream_event(event, state=state, **stream_options)


__all__ = ["normalize_stream", "normalize_stream_event"]
