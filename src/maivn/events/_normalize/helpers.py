"""Shared normalization helpers."""

from __future__ import annotations

from typing import Any

from .._models import AppEvent

# MARK: Text and Mapping Helpers


def clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def clean_stream_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value else None


def coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    return {}


def get_latest_response_text(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in reversed(value):
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


def model_result_as_mapping(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return dict(value)
    return None


def validate_payload(payload: dict[str, Any]) -> AppEvent:
    return AppEvent.model_validate(payload)
