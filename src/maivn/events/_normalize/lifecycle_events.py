"""Lifecycle and terminal event normalization handlers."""

from __future__ import annotations

import uuid
from typing import Any

from ..._internal.utils.reporting.app_event_payloads import (
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_tool_event_payload,
)
from .._models import NormalizedStreamState
from .context import NormalizationOptions
from .helpers import (
    clean_text,
    coerce_mapping,
    get_latest_response_text,
    model_result_as_mapping,
)

# MARK: Lifecycle Events


def handle_enrichment_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    phase = clean_text(payload.get("phase"))
    message = clean_text(payload.get("message"))
    if phase is None or message is None:
        return []
    return [
        build_enrichment_payload(
            phase=phase,
            message=message,
            scope_id=clean_text(payload.get("scope_id")),
            scope_name=clean_text(payload.get("scope_name")),
            scope_type=clean_text(payload.get("scope_type")),
            memory=coerce_mapping(payload.get("memory")) or None,
            redaction=coerce_mapping(payload.get("redaction")) or None,
            **options.participant_kwargs(),
        )
    ]


def handle_interrupt_required_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[dict[str, Any]]:
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
            choices=choices if isinstance(choices, list) else None,
            timestamp=clean_text(payload.get("timestamp")),
        )
    ]


# MARK: Terminal Events


def handle_final_event(
    payload: dict[str, Any],
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    final_result = payload.get("result")
    structured_output = model_result_as_mapping(final_result) or state.last_model_tool_result
    normalized_payloads: list[dict[str, Any]] = []

    if state.pending_model_tools:
        for pending in state.pending_model_tools[:-1]:
            normalized_payloads.append(
                build_tool_event_payload(
                    tool_name=pending["tool_name"],
                    tool_id=pending["tool_id"],
                    status="completed",
                    result=None,
                    agent_name=options.default_agent_name,
                    swarm_name=options.default_swarm_name,
                    tool_type="model",
                    **options.participant_kwargs(),
                )
            )

        last_pending = state.pending_model_tools[-1] if state.pending_model_tools else None
        if last_pending is not None:
            normalized_payloads.append(
                build_tool_event_payload(
                    tool_name=last_pending["tool_name"],
                    tool_id=last_pending["tool_id"],
                    status="completed",
                    result=structured_output,
                    agent_name=options.default_agent_name,
                    swarm_name=options.default_swarm_name,
                    tool_type="model",
                    **options.participant_kwargs(),
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
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[dict[str, Any]]:
    return [
        build_error_payload(
            error=clean_text(payload.get("error")) or "Unknown error",
            details=coerce_mapping(payload.get("details")) or None,
        )
    ]


def handle_session_start_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[dict[str, Any]]:
    session_id = clean_text(payload.get("session_id"))
    assistant_id = clean_text(payload.get("assistant_id"))
    if session_id is None or assistant_id is None:
        return []
    return [build_session_start_payload(session_id=session_id, assistant_id=assistant_id)]
