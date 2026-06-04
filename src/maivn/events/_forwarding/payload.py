"""Payload extraction and coercion helpers for normalized event forwarding."""

# pyright: strict
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias, cast

from .._models import AppEvent

# MARK: Types


EventPayload: TypeAlias = dict[str, object]
ToolArguments: TypeAlias = dict[str, object]


# MARK: Tool Payload


@dataclass(frozen=True)
class ToolPayload:
    tool_id: str | None
    tool_name: str | None
    tool_type: str | None
    status: str | None
    args: ToolArguments | None
    result: object
    error: str | None
    agent_name: str | None
    swarm_name: str | None


# MARK: Payload Extraction


def extract_tool_payload(
    event: AppEvent,
    *,
    payload: EventPayload,
) -> ToolPayload:
    tool = event.tool
    tool_id = (
        normalized_text(payload.get("tool_id"))
        or normalized_text(payload.get("event_id"))
        or normalized_text(tool.id if tool is not None else None)
    )
    tool_name = (
        normalized_text(payload.get("tool_name"))
        or normalized_text(payload.get("tool_type"))
        or normalized_text(tool.name if tool is not None else None)
    )
    tool_type = normalized_text(payload.get("tool_type")) or normalized_text(
        tool.type if tool is not None else None
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        tool.status if tool is not None else None
    )
    args = coerce_mapping(payload.get("args")) or coerce_mapping(payload.get("params"))
    if args is None and tool is not None:
        args = coerce_mapping(cast(object, tool.args))
    result = payload.get("result", cast(object, tool.result) if tool is not None else None)
    error = normalized_text(payload.get("error")) or normalized_text(
        tool.error if tool is not None else None
    )
    agent_name = normalized_text(payload.get("agent_name"))
    swarm_name = normalized_text(payload.get("swarm_name"))
    scope = event.scope
    if agent_name is None and scope is not None and scope.type == "agent":
        agent_name = normalized_text(scope.name)
    if swarm_name is None and scope is not None and scope.type == "swarm":
        swarm_name = normalized_text(scope.name)
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


def normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def coerce_mapping(value: object) -> ToolArguments | None:
    if not isinstance(value, dict):
        return None
    mapping = cast(dict[object, object], value)
    return {cast(str, key): item for key, item in mapping.items()}


def mapping_value(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        return cast(ToolArguments, value).get(key)
    return None


def string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    return [str(item) for item in items]


def integer_value(value: object) -> int | None:
    return value if isinstance(value, int) else None


def float_value(value: object) -> float | None:
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
    "EventPayload",
    "ToolPayload",
    "ToolArguments",
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
