"""Deduplication helpers for EventBridge."""

from __future__ import annotations

from typing import Any

# MARK: Fingerprints


def normalize_dedup_part(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def build_interrupt_fingerprint(
    *,
    prompt: str,
    data_key: str,
    arg_name: str | None = None,
) -> tuple[str, str]:
    """Stable fingerprint for an interrupt's logical identity."""
    return (
        normalize_dedup_part(prompt),
        normalize_dedup_part(arg_name) or normalize_dedup_part(data_key),
    )


def build_status_fingerprint(data: dict[str, Any]) -> tuple[str, str] | None:
    """Fingerprint for a ``status_message`` payload, or ``None`` if uniqueable."""
    message = data.get("message")
    if not isinstance(message, str):
        return None
    normalized_message = message.strip()
    if not normalized_message:
        return None

    assistant_id = data.get("assistant_id")
    normalized_assistant_id = (
        assistant_id.strip().lower()
        if isinstance(assistant_id, str) and assistant_id.strip()
        else ""
    )
    return normalized_assistant_id, normalized_message


__all__ = [
    "build_interrupt_fingerprint",
    "build_status_fingerprint",
    "normalize_dedup_part",
]
