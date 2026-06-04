# pyright: strict
"""Shared normalization helpers."""

from __future__ import annotations

from typing import Protocol, cast, runtime_checkable

from .._models import AppEvent, JsonObject

# MARK: Types


@runtime_checkable
class SupportsModelDump(Protocol):
    def model_dump(self) -> object: ...


# MARK: Text and Mapping Helpers


def _coerce_dict(value: dict[object, object]) -> JsonObject:
    return cast(JsonObject, dict(value))


def _coerce_model_dump(value: SupportsModelDump) -> JsonObject | None:
    dumped = value.model_dump()
    if isinstance(dumped, dict):
        return cast(JsonObject, dumped)
    return None


def clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def clean_stream_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value else None


def coerce_mapping(value: object) -> JsonObject:
    if isinstance(value, dict):
        return _coerce_dict(cast(dict[object, object], value))
    if isinstance(value, SupportsModelDump):
        dumped = _coerce_model_dump(value)
        if dumped is not None:
            return dumped
    return {}


def get_latest_response_text(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    for item in reversed(items):
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                return cleaned
    return None


def compute_delta(previous: str, current: str) -> str:
    if not current:
        return ""
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous) :]
    if previous.startswith(current):
        return ""
    return current


# MARK: Payload Helpers


def map_assignment_status(raw_status: str | None) -> str:
    value = (raw_status or "").strip().lower()
    if value in {"received", "assigned", "queued"}:
        return "received"
    if value in {"completed", "done", "finished", "success"}:
        return "completed"
    if value in {"failed", "error"}:
        return "failed"
    return "in_progress"


def model_result_as_mapping(value: object) -> JsonObject | None:
    if isinstance(value, SupportsModelDump):
        dumped = _coerce_model_dump(value)
        if dumped is not None:
            return dumped
    if isinstance(value, dict):
        return _coerce_dict(cast(dict[object, object], value))
    return None


def validate_payload(payload: JsonObject) -> AppEvent:
    return AppEvent.model_validate(payload)
