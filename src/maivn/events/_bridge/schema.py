"""Lightweight schema validation for events emitted via :class:`EventBridge`.

The bridge accepts arbitrary ``(event_type, data)`` tuples — flexible by
design, but typos and malformed payloads can reach the wire silently
without a guardrail. This module is that guardrail:

* a top-level *envelope* check (non-empty type, dict data, no protected
  field names like ``__class__`` / ``__proto__``),
* per-known-type required-field checks for the AppEvent v1 surface,
* configurable strictness — ``"warn"`` (default) logs a warning and lets
  the event through; ``"strict"`` raises ``EventSchemaError``.

This is intentionally a *guardrail*, not a full schema definition. The
canonical payload shapes are defined by the builders in
``maivn._internal.utils.reporting.app_event_payloads`` — duplicating them
as full pydantic models would couple the SDK boundary too tightly to
internal payload evolution. The validator focuses on catching the kinds
of mistakes that would otherwise reach a frontend developer at 2am.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Literal

ValidationMode = Literal["off", "warn", "strict"]
_VALID_MODES: frozenset[str] = frozenset({"off", "warn", "strict"})

_logger = logging.getLogger("maivn.events._bridge.schema")

# MARK: Errors


class EventSchemaError(ValueError):
    """Raised in ``strict`` mode when an emitted event fails validation."""


# MARK: Required fields per known event type

# Each entry lists the *minimum* required fields that must be present and
# non-None on the data payload for the event to be considered structurally
# valid. The bridge's normalization layer fills in derived fields, so this
# list is tight on purpose: keep it to fields that downstream consumers
# (frontend, observers) absolutely depend on.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "tool_event": ("tool_id", "tool_name", "status"),
    "system_tool_start": ("tool_id", "tool_type"),
    "system_tool_chunk": ("tool_id", "text"),
    "system_tool_complete": ("tool_id", "result"),
    "assistant_chunk": ("assistant_id", "text"),
    "status_message": ("assistant_id", "message"),
    "interrupt_required": ("interrupt_id", "data_key", "prompt"),
    "agent_assignment": ("agent_name", "status"),
    "enrichment": ("phase", "message"),
    "final": ("response",),
    "error": ("error",),
    "session_start": ("session_id",),
    "session_end": ("session_id",),
}

# Field names that look like a deserialization-attack vector if they appear
# at any depth. Refusing them does not break legitimate payloads — these
# names are reserved by JS / Python runtimes and have no place in event
# payloads.
_RESERVED_FIELD_NAMES: frozenset[str] = frozenset({"__class__", "__proto__", "constructor"})


# MARK: Public API


def validate_event(
    event_type: str,
    data: Mapping[str, Any],
    *,
    mode: ValidationMode = "warn",
) -> None:
    """Validate an event tuple against the bridge's schema guardrails.

    Behavior depends on ``mode``:

    * ``"off"``: returns immediately. Useful for hot paths or when the
      caller has already validated upstream.
    * ``"warn"`` (default): logs a structured warning on the first
      violation and returns. Production-safe.
    * ``"strict"``: raises :class:`EventSchemaError` on any violation.
      Recommended for tests and developer environments.
    """
    if mode == "off":
        return
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")

    problems = list(_iter_problems(event_type, data))
    if not problems:
        return

    message = f"Event {event_type!r} failed schema validation: {'; '.join(problems)}"
    if mode == "strict":
        raise EventSchemaError(message)
    _logger.warning(message)


def _iter_problems(event_type: str, data: Mapping[str, Any]):
    if not isinstance(event_type, str):
        yield f"event_type must be str, got {type(event_type).__name__}"
        return
    if not event_type.strip():
        yield "event_type must be non-empty"

    if not isinstance(data, Mapping):
        yield f"data must be a mapping, got {type(data).__name__}"
        return

    reserved_hits = list(_iter_reserved_hits(data))
    if reserved_hits:
        yield "reserved field names present at " + ", ".join(reserved_hits)

    required = _REQUIRED_FIELDS.get(event_type)
    if required is None:
        return
    missing = [field for field in required if data.get(field) in (None, "")]
    if missing:
        yield f"missing required fields {missing}"


def _iter_reserved_hits(value: Any, *, path: str = "$"):
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{path}.{key}"
            if key in _RESERVED_FIELD_NAMES:
                yield here
            yield from _iter_reserved_hits(item, path=here)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_reserved_hits(item, path=f"{path}[{index}]")


__all__ = [
    "EventSchemaError",
    "ValidationMode",
    "validate_event",
]
