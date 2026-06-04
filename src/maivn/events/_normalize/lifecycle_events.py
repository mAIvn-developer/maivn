# pyright: strict
"""Lifecycle and terminal event normalization handlers."""

from __future__ import annotations

import logging
import uuid

from maivn_shared import resolve_enrichment_message

from ..._internal.utils.reporting.app_event_payloads import (
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_tool_event_payload,
)
from .._models import JsonObject, NormalizedStreamState
from .context import NormalizationOptions
from .helpers import (
    clean_text,
    coerce_mapping,
    get_latest_response_text,
    model_result_as_mapping,
)

_logger = logging.getLogger("maivn.events._normalize.lifecycle")

# MARK: Lifecycle Events


def handle_enrichment_event(
    payload: JsonObject,
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    phase = clean_text(payload.get("phase"))
    if phase is None:
        # A phase-less enrichment event cannot be rendered or ordered; drop it
        # but leave a trace so a regressed emitter is diagnosable rather than
        # silently swallowed.
        _logger.debug("Dropping enrichment event with no phase: %r", payload)
        return []
    # Never drop on a missing message — derive one from the phase so a
    # message-less emit still advances the UI instead of vanishing.
    message = clean_text(payload.get("message")) or resolve_enrichment_message(phase)
    reevaluate = coerce_mapping(payload.get("reevaluate")) or None

    # Reevaluate restarts synthesis on a fresh server-side thread. The next
    # ``streaming_content`` may arrive under a NEW ``assistant_id`` (each
    # thread can mint its own), so per-ID tracking is unreliable. Instead
    # we clear the cached text and set a one-shot global flag so the
    # NEXT streamed chunk — whatever assistant_id it carries — is emitted
    # with ``replace_content=True`` on the wire. The UI overwrites the
    # bubble content for that chunk, and subsequent chunks append normally
    # within the new cycle.
    if phase == "reevaluate_accrued":
        state.streaming_text_by_id.clear()
        state.next_assistant_chunk_replaces = True

    return [
        build_enrichment_payload(
            phase=phase,
            message=message,
            scope_id=clean_text(payload.get("scope_id")),
            scope_name=clean_text(payload.get("scope_name")),
            scope_type=clean_text(payload.get("scope_type")),
            memory=coerce_mapping(payload.get("memory")) or None,
            redaction=coerce_mapping(payload.get("redaction")) or None,
            reevaluate=reevaluate,
            **options.participant_kwargs(),
        )
    ]


def handle_interrupt_required_event(
    payload: JsonObject,
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    interrupt_number = payload.get("interrupt_number")
    total_interrupts = payload.get("total_interrupts")
    choices = payload.get("choices")
    return [
        build_interrupt_required_payload(
            interrupt_id=clean_text(payload.get("interrupt_id")) or str(uuid.uuid4()),
            checkpoint_id=clean_text(payload.get("checkpoint_id")),
            data_key=clean_text(payload.get("data_key")) or "input",
            prompt=clean_text(payload.get("prompt")) or "Input required.",
            tool_name=clean_text(payload.get("tool_name")),
            arg_name=clean_text(payload.get("arg_name")),
            assignment_id=clean_text(payload.get("assignment_id")),
            interrupt_number=interrupt_number if isinstance(interrupt_number, int) else None,
            total_interrupts=total_interrupts if isinstance(total_interrupts, int) else None,
            input_type=clean_text(payload.get("input_type")),
            choices=[str(choice) for choice in choices] if isinstance(choices, list) else None,
            timestamp=clean_text(payload.get("timestamp")),
        )
    ]


# MARK: Terminal Events


def _build_pending_model_tool_completion(
    pending: dict[str, str],
    *,
    result: JsonObject | None,
    options: NormalizationOptions,
) -> JsonObject:
    return build_tool_event_payload(
        tool_name=pending["tool_name"],
        tool_id=pending["tool_id"],
        status="completed",
        result=result,
        agent_name=options.default_agent_name,
        swarm_name=options.default_swarm_name,
        tool_type="model",
        **options.participant_kwargs(),
    )


def handle_final_event(
    payload: JsonObject,
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[JsonObject]:
    final_result = payload.get("result")
    structured_output = model_result_as_mapping(final_result) or state.last_model_tool_result
    normalized_payloads: list[JsonObject] = []

    if state.pending_model_tools:
        for pending in state.pending_model_tools[:-1]:
            normalized_payloads.append(
                _build_pending_model_tool_completion(
                    pending,
                    result=None,
                    options=options,
                )
            )

        normalized_payloads.append(
            _build_pending_model_tool_completion(
                state.pending_model_tools[-1],
                result=structured_output,
                options=options,
            )
        )
        state.pending_model_tools.clear()

    response_text = (
        get_latest_response_text(payload.get("responses"))
        or clean_text(payload.get("response"))
        or ""
    )
    normalized_payloads.append(
        build_final_payload(
            response=response_text,
            result=final_result,
            token_usage=coerce_mapping(payload.get("token_usage")) or None,
        )
    )
    return normalized_payloads


def handle_error_event(
    payload: JsonObject,
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    return [
        build_error_payload(
            error=clean_text(payload.get("error")) or "Unknown error",
            details=coerce_mapping(payload.get("details")) or None,
        )
    ]


def handle_session_start_event(
    payload: JsonObject,
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    session_id = clean_text(payload.get("session_id"))
    assistant_id = clean_text(payload.get("assistant_id"))
    if session_id is None or assistant_id is None:
        return []
    return [
        build_session_start_payload(
            session_id=session_id,
            assistant_id=assistant_id,
        )
    ]
