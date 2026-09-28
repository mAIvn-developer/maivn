"""System tool normalization handlers."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from maivn._internal.models import tool_card_id
from maivn._internal.reporting.app_event_payloads import (
    build_system_tool_chunk_payload,
    build_tool_event_payload,
)

from .helpers import clean_stream_text, clean_text, coerce_mapping

if TYPE_CHECKING:
    from maivn.events._models import JsonObject, NormalizedStreamState

    from .context import NormalizationOptions

# MARK: System Tool Streaming


def handle_system_tool_start_event(
    payload: JsonObject,
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    tool_call = coerce_mapping(payload.get('tool_call'))
    spec_ref = coerce_mapping(tool_call.get('spec_ref'))
    registered_tool_id = clean_text(spec_ref.get('tool_id'))
    raw_tool_name = clean_text(payload.get('tool_name'))
    metadata = (
        options.tool_metadata_map.get(registered_tool_id)
        if registered_tool_id and options.tool_metadata_map
        else None
    )
    if metadata is None and raw_tool_name and options.tool_metadata_map:
        metadata = options.tool_metadata_map.get(raw_tool_name)
    metadata_name = clean_text(metadata.get('tool_name')) if isinstance(metadata, dict) else None
    metadata_type = clean_text(metadata.get('tool_type')) if isinstance(metadata, dict) else None
    tool_name = raw_tool_name or metadata_name or 'system_tool'
    # Shared with hook targeting: a hook firing that attaches to this tool's
    # card must resolve the same id, so the precedence lives in one place.
    tool_id = tool_card_id(payload) or str(uuid.uuid4())
    # ``system_tool_start`` is the canonical wire taxonomy for every
    # executed tool, not a semantic UI type. Only explicit contract metadata
    # may classify a call as a system tool; ordinary SDK tools default to func.
    tool_type = (metadata_type or 'func').lower()
    agent_name = clean_text(payload.get('agent_name')) or options.default_agent_name
    swarm_name = clean_text(payload.get('swarm_name')) or options.default_swarm_name
    args = coerce_mapping(payload.get('params'))
    if not args:
        args = coerce_mapping(tool_call.get('arguments')) or coerce_mapping(tool_call.get('args'))
    raw_private_data_keys = payload.get('private_data_keys')
    if not raw_private_data_keys and isinstance(metadata, dict):
        raw_private_data_keys = metadata.get('private_data_keys')
    private_data_keys = (
        [key for key in raw_private_data_keys if isinstance(key, str) and key]
        if isinstance(raw_private_data_keys, list)
        else []
    )
    state.started_system_tools[tool_id] = tool_type
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status='executing',
            args=args,
            agent_name=agent_name,
            swarm_name=swarm_name,
            tool_type=tool_type,
            private_data_keys=private_data_keys,
            **options.participant_kwargs(),
        )
    ]


def handle_system_tool_chunk_event(
    payload: JsonObject,
    state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    tool_id = (
        clean_text(payload.get('assignment_id'))
        or clean_text(payload.get('tool_id'))
        or clean_text(payload.get('correlation_id'))
    )
    if tool_id is None and len(state.started_system_tools) == 1:
        tool_id = next(iter(state.started_system_tools))
    tool_id = tool_id or 'system_tool'
    progress = payload.get('progress')
    text = clean_stream_text(payload.get('text'))
    if text is None:
        return []
    return [
        build_system_tool_chunk_payload(
            tool_id=tool_id,
            text=text,
            progress=progress if isinstance(progress, (int, float)) else None,
        )
    ]


def handle_system_tool_complete_event(
    payload: JsonObject,
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    outcome = coerce_mapping(payload.get('outcome'))
    state.last_tool_result = coerce_mapping(payload.get('result', outcome.get('result'))) or None
    tool_name = clean_text(payload.get('tool_name')) or 'system_tool'
    tool_id = (
        clean_text(payload.get('assignment_id'))
        or clean_text(payload.get('tool_id'))
        or clean_text(payload.get('correlation_id'))
        or clean_text(outcome.get('call_id'))
        or tool_name
    )
    duration_ms = _duration_ms(payload, outcome)
    tool_type = state.started_system_tools.pop(tool_id, 'system')
    agent_name = clean_text(payload.get('agent_name')) or options.default_agent_name
    swarm_name = clean_text(payload.get('swarm_name')) or options.default_swarm_name
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status='completed',
            result=payload.get('result', outcome.get('result')),
            agent_name=agent_name,
            swarm_name=swarm_name,
            tool_type=tool_type,
            duration_ms=duration_ms,
            **options.participant_kwargs(),
        )
    ]


def _duration_ms(payload: JsonObject, outcome: JsonObject) -> int | None:
    """Return a non-negative authoritative tool duration from either wire shape."""
    for value in (
        payload.get('duration_ms'),
        payload.get('latency_ms'),
        outcome.get('duration_ms'),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def handle_system_tool_error_event(
    payload: JsonObject,
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    outcome = coerce_mapping(payload.get('outcome'))
    tool_name = clean_text(payload.get('tool_name')) or 'system_tool'
    tool_id = (
        clean_text(payload.get('assignment_id'))
        or clean_text(payload.get('tool_id'))
        or clean_text(payload.get('correlation_id'))
        or clean_text(outcome.get('call_id'))
        or tool_name
    )
    tool_type = state.started_system_tools.pop(tool_id, 'system')
    agent_name = clean_text(payload.get('agent_name')) or options.default_agent_name
    swarm_name = clean_text(payload.get('swarm_name')) or options.default_swarm_name
    outcome_error = coerce_mapping(outcome.get('error'))
    error = (
        clean_text(payload.get('error'))
        or clean_text(outcome.get('error'))
        or clean_text(outcome_error.get('message'))
        or clean_text(outcome_error.get('code'))
        # The durable projection strips the message off most errors but always
        # carries the stable code under `error_code`; surfacing it is what keeps
        # a failed tool from reading as the bare string "Unknown error".
        or clean_text(outcome_error.get('error_code'))
        or 'Unknown error'
    )
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status='failed',
            error=error,
            agent_name=agent_name,
            swarm_name=swarm_name,
            tool_type=tool_type,
            duration_ms=_duration_ms(payload, outcome),
            **options.participant_kwargs(),
        )
    ]
