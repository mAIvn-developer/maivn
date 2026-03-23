"""Tool event normalization handlers."""

from __future__ import annotations

import uuid
from typing import Any

from ..._internal.utils.reporting.app_event_payloads import build_tool_event_payload
from .._models import NormalizedStreamState
from .context import NormalizationOptions
from .helpers import clean_text, coerce_mapping, model_result_as_mapping
from .tooling import (
    extract_tool_args,
    extract_tool_identifier,
    extract_tool_name,
    extract_tool_scope,
    extract_tool_type,
)

# MARK: Tool Execution


def handle_tool_event(
    payload: dict[str, Any],
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    value = coerce_mapping(payload.get("value"))
    raw_tool_calls = value.get("tool_calls")
    tool_calls = (
        [tool_call for tool_call in raw_tool_calls if isinstance(tool_call, dict)]
        if isinstance(raw_tool_calls, list)
        else []
    )
    if not tool_calls:
        single = value.get("tool_call")
        if isinstance(single, dict):
            tool_calls = [single]

    normalized_payloads: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        tool_id = extract_tool_identifier(tool_call)
        if not tool_id or tool_id in state.reported_tool_ids:
            continue
        state.reported_tool_ids.add(tool_id)

        resolved_tool_name = extract_tool_name(tool_call, tool_id, options)
        resolved_tool_type = extract_tool_type(tool_call, tool_id, options)
        resolved_agent_name, resolved_swarm_name = extract_tool_scope(
            tool_id,
            tool_type=resolved_tool_type,
            tool_name=resolved_tool_name,
            options=options,
        )
        normalized_payloads.append(
            build_tool_event_payload(
                tool_name=resolved_tool_name,
                tool_id=tool_id,
                status="executing",
                args=extract_tool_args(
                    tool_call,
                    tool_id,
                    tool_type=resolved_tool_type,
                    options=options,
                ),
                agent_name=resolved_agent_name,
                swarm_name=resolved_swarm_name,
                tool_type=resolved_tool_type,
                **options.participant_kwargs(),
            )
        )
    return normalized_payloads


def handle_model_tool_complete_event(
    payload: dict[str, Any],
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    tool_name = clean_text(payload.get("tool_name")) or "model_tool"
    tool_id = clean_text(payload.get("event_id")) or str(uuid.uuid4())
    state.pending_model_tools.append({"tool_name": tool_name, "tool_id": tool_id})
    state.last_model_tool_result = model_result_as_mapping(payload.get("result"))
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status="executing",
            result=None,
            agent_name=options.default_agent_name,
            swarm_name=options.default_swarm_name,
            tool_type="model",
            **options.participant_kwargs(),
        )
    ]
