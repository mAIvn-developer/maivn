# pyright: strict
"""Assistant-facing event normalization handlers."""

from __future__ import annotations

from ..._internal.utils.reporting.app_event_payloads import (
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_status_message_chunk_payload,
    build_status_message_payload,
)
from .._models import JsonObject, NormalizedStreamState
from .context import NormalizationOptions
from .helpers import clean_stream_text, clean_text, compute_delta, map_assignment_status

# MARK: Assistant Streaming


def _resolve_swarm_agent_name(
    payload: JsonObject,
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
    payload: JsonObject,
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    normalized_payloads: list[JsonObject] = []
    streaming_content = clean_stream_text(payload.get("streaming_content"))
    if streaming_content is not None:
        assistant_id = clean_text(payload.get("assistant_id")) or "assistant"
        previous = state.streaming_text_by_id.get(assistant_id, "")
        delta = compute_delta(previous, streaming_content)
        # Detect a divergent stream: we had prior text and the new cumulative
        # text doesn't continue from it. This covers reevaluate cycles AND
        # any other synthesis restart (evaluate-node re-entry, retries) that
        # didn't fire a ``reevaluate_accrued`` enrichment event. Either case
        # should overwrite the bubble for this chunk rather than append.
        diverged_stream = (
            bool(previous)
            and bool(streaming_content)
            and not streaming_content.startswith(previous)
            and not previous.startswith(streaming_content)
        )
        state.streaming_text_by_id[assistant_id] = streaming_content
        replace_content = diverged_stream
        if state.next_assistant_chunk_replaces:
            replace_content = True
            state.next_assistant_chunk_replaces = False
        # When replacing the bubble, the downstream UI overwrites with this
        # chunk's text — so it must be the FULL new cumulative content, not
        # the suffix-after-shared-prefix that ``compute_delta`` returns for
        # partial-overlap snapshots. Without this override the bubble shows
        # only a fragment of the new synthesis.
        if replace_content and streaming_content:
            delta = streaming_content
        if delta:
            normalized_payloads.append(
                build_assistant_chunk_payload(
                    assistant_id=assistant_id,
                    text=delta,
                    participant_key=options.default_participant_key,
                    participant_name=options.default_participant_name,
                    participant_role=options.default_participant_role,
                    replace_content=replace_content,
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
    payload: JsonObject,
    _state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    text = clean_stream_text(payload.get("text"))
    if text is None:
        return []
    return [
        build_assistant_chunk_payload(
            assistant_id=clean_text(payload.get("assistant_id")) or "assistant",
            text=text,
            participant_key=options.default_participant_key,
            participant_name=options.default_participant_name,
            participant_role=options.default_participant_role,
        )
    ]


def handle_status_message_event(
    payload: JsonObject,
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    message = clean_text(payload.get("message"))
    if message is None:
        return []
    return [
        build_status_message_payload(
            assistant_id=clean_text(payload.get("assistant_id")) or "assistant",
            message=message,
        )
    ]


def handle_status_message_chunk_event(
    payload: JsonObject,
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    text = clean_stream_text(payload.get("text"))
    final = payload.get("final") is True or payload.get("is_final") is True
    if text is None and not final:
        return []

    assistant_id = clean_text(payload.get("assistant_id")) or "assistant"
    status_id = clean_text(payload.get("status_id")) or f"status:{assistant_id}"
    return [
        build_status_message_chunk_payload(
            assistant_id=assistant_id,
            status_id=status_id,
            text=text or "",
            final=final,
        )
    ]
