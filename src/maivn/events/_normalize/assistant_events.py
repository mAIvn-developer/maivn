"""Assistant-facing event normalization handlers."""

from __future__ import annotations

from typing import Any

from ..._internal.utils.reporting.app_event_payloads import (
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_status_message_payload,
)
from .._models import NormalizedStreamState
from .context import NormalizationOptions
from .helpers import clean_stream_text, clean_text, compute_delta, map_assignment_status

# MARK: Assistant Streaming


def _resolve_swarm_agent_name(
    payload: dict[str, Any],
    options: NormalizationOptions,
) -> str:
    """Resolve the display name for a swarm-agent assignment update."""
    action_name = clean_text(payload.get("action_name"))
    if action_name is not None:
        return action_name

    action_id = clean_text(payload.get("action_id"))
    if action_id and options.assignment_name_map and action_id in options.assignment_name_map:
        return options.assignment_name_map[action_id]

    if action_id is not None:
        return action_id

    return "unknown-agent"


def handle_update_event(
    payload: dict[str, Any],
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    normalized_payloads: list[dict[str, Any]] = []
    streaming_content = clean_stream_text(payload.get("streaming_content"))
    if streaming_content is not None:
        assistant_id = clean_text(payload.get("assistant_id")) or "assistant"
        previous = state.streaming_text_by_id.get(assistant_id, "")
        delta = compute_delta(previous, streaming_content)
        state.streaming_text_by_id[assistant_id] = streaming_content
        if delta:
            normalized_payloads.append(
                build_assistant_chunk_payload(
                    assistant_id=assistant_id,
                    text=delta,
                    **options.participant_kwargs(),
                )
            )

    action_type = clean_text(payload.get("action_type"))
    if action_type == "swarm_agent":
        assignment_status = map_assignment_status(clean_text(payload.get("status")))
        action_name = _resolve_swarm_agent_name(payload, options)
        normalized_payloads.append(
            build_agent_assignment_payload(
                agent_name=action_name,
                status=assignment_status,
                assignment_id=clean_text(payload.get("action_id")),
                swarm_name=clean_text(payload.get("swarm_name")) or options.default_swarm_name,
                task=clean_text(payload.get("task")),
                result=payload.get("result") if assignment_status == "completed" else None,
                **options.participant_kwargs(),
            )
        )

    return normalized_payloads


def handle_progress_update_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    text = clean_stream_text(payload.get("text"))
    if text is None:
        return []
    return [
        build_assistant_chunk_payload(
            assistant_id=clean_text(payload.get("assistant_id")) or "assistant",
            text=text,
            **options.participant_kwargs(),
        )
    ]


def handle_status_message_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[dict[str, Any]]:
    message = clean_text(payload.get("message"))
    if message is None:
        return []
    return [
        build_status_message_payload(
            assistant_id=clean_text(payload.get("assistant_id")) or "assistant",
            message=message,
        )
    ]
