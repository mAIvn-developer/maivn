from __future__ import annotations

import json
from typing import Any

# MARK: Text Helpers


def normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def normalize_key_part(value: Any) -> str | None:
    normalized = normalize_text(value)
    return normalized.lower() if normalized is not None else None


# MARK: Mapping Helpers


def coerce_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def merge_extra_fields(
    normalized_payload: dict[str, Any],
    original_payload: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(normalized_payload)
    for key, value in original_payload.items():
        if key not in merged:
            merged[key] = value
    return merged


# MARK: Identity Helpers


def fingerprint_mapping(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(value)


def slugify(value: str | None) -> str | None:
    normalized = normalize_key_part(value)
    if normalized is None:
        return None
    return "_".join(normalized.split())


def build_fallback_id(prefix: str, *parts: str | None) -> str:
    normalized_parts = [part for part in (slugify(part) for part in parts) if part]
    if not normalized_parts:
        return prefix
    return ":".join([prefix, *normalized_parts])
