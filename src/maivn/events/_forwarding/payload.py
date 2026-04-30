"""Payload extraction and coercion helpers for normalized event forwarding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .._models import AppEvent

# MARK: Tool Payload


@dataclass(frozen=True)
class ToolPayload:
    tool_id: str | None
    tool_name: str | None
    tool_type: str | None
    status: str | None
    args: dict[str, Any] | None
    result: Any
    error: str | None
    agent_name: str | None
    swarm_name: str | None


# MARK: Payload Extraction


def extract_tool_payload(
    event: AppEvent,
    *,
    payload: dict[str, Any],
) -> ToolPayload:
    tool = event.tool
    tool_id = (
        normalized_text(payload.get("tool_id"))
        or normalized_text(payload.get("event_id"))
        or normalized_text(getattr(tool, "id", None))
    )
    tool_name = (
        normalized_text(payload.get("tool_name"))
        or normalized_text(payload.get("tool_type"))
        or normalized_text(getattr(tool, "name", None))
    )
    tool_type = normalized_text(payload.get("tool_type")) or normalized_text(
        getattr(tool, "type", None)
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        getattr(tool, "status", None)
    )
    args = coerce_mapping(payload.get("args")) or coerce_mapping(payload.get("params"))
    if args is None and tool is not None:
        args = dict(tool.args)
    result = payload.get("result", getattr(tool, "result", None))
    error = normalized_text(payload.get("error")) or normalized_text(getattr(tool, "error", None))
    agent_name = normalized_text(payload.get("agent_name"))
    swarm_name = normalized_text(payload.get("swarm_name"))
    if agent_name is None and getattr(event.scope, "type", None) == "agent":
        agent_name = normalized_text(getattr(event.scope, "name", None))
    if swarm_name is None and getattr(event.scope, "type", None) == "swarm":
        swarm_name = normalized_text(getattr(event.scope, "name", None))
    return ToolPayload(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type=tool_type,
        status=status,
        args=args,
        result=result,
        error=error,
        agent_name=agent_name,
        swarm_name=swarm_name,
    )


# MARK: Coercion


def normalized_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def string_value(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def coerce_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return dict(value)


def mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def integer_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def float_value(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


# MARK: Tool Normalization


def normalize_tool_type(tool_type: str | None) -> str:
    return (normalized_text(tool_type) or "func").lower()


def normalize_tool_status(status: str | None) -> str:
    normalized = (normalized_text(status) or "executing").lower()
    if normalized in {"started", "running", "in_progress", "pending"}:
        return "executing"
    if normalized in {"completed", "success"}:
        return "completed"
    if normalized in {"failed", "error"}:
        return "failed"
    return normalized


__all__ = [
    "ToolPayload",
    "coerce_mapping",
    "extract_tool_payload",
    "float_value",
    "integer_value",
    "mapping_value",
    "normalize_tool_status",
    "normalize_tool_type",
    "normalized_text",
    "string_list",
    "string_value",
]
