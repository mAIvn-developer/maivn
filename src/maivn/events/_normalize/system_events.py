"""System tool normalization handlers."""

from __future__ import annotations

import uuid
from typing import Any

from ..._internal.utils.reporting.app_event_payloads import (
    build_system_tool_chunk_payload,
    build_tool_event_payload,
)
from .._models import NormalizedStreamState
from .context import NormalizationOptions
from .helpers import clean_stream_text, clean_text, coerce_mapping

# MARK: System Tool Streaming


def handle_system_tool_start_event(
    payload: dict[str, Any],
    state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    tool_name = clean_text(payload.get("tool_name")) or "system_tool"
    tool_id = (
        clean_text(payload.get("assignment_id"))
        or clean_text(payload.get("tool_id"))
        or str(uuid.uuid4())
    )
    state.started_system_tools[tool_id] = tool_id
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status="executing",
            args=coerce_mapping(payload.get("params")),
            agent_name=options.default_agent_name,
            swarm_name=options.default_swarm_name,
            tool_type="system",
            **options.participant_kwargs(),
        )
    ]


def handle_system_tool_chunk_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[dict[str, Any]]:
    tool_id = (
        clean_text(payload.get("assignment_id"))
        or clean_text(payload.get("tool_id"))
        or "system_tool"
    )
    progress = payload.get("progress")
    text = clean_stream_text(payload.get("text"))
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
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    tool_name = clean_text(payload.get("tool_name")) or "system_tool"
    tool_id = (
        clean_text(payload.get("assignment_id")) or clean_text(payload.get("tool_id")) or tool_name
    )
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status="completed",
            result=payload.get("result"),
            agent_name=options.default_agent_name,
            swarm_name=options.default_swarm_name,
            tool_type="system",
            **options.participant_kwargs(),
        )
    ]


def handle_system_tool_error_event(
    payload: dict[str, Any],
    _state: NormalizedStreamState,
    options: NormalizationOptions,
) -> list[dict[str, Any]]:
    tool_name = clean_text(payload.get("tool_name")) or "system_tool"
    tool_id = (
        clean_text(payload.get("assignment_id")) or clean_text(payload.get("tool_id")) or tool_name
    )
    return [
        build_tool_event_payload(
            tool_name=tool_name,
            tool_id=tool_id,
            status="failed",
            error=clean_text(payload.get("error")) or "Unknown error",
            agent_name=options.default_agent_name,
            swarm_name=options.default_swarm_name,
            tool_type="system",
            **options.participant_kwargs(),
        )
    ]
