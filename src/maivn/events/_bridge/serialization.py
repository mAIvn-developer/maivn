"""Serialization helpers for bridge SSE payloads."""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

logger = logging.getLogger("maivn.events._bridge")


# MARK: JSON Helpers


class _BridgeJSONEncoder(json.JSONEncoder):
    """Tolerant encoder for common Python value types we cannot leak to ``str``.

    Prefers structured representations over ``__str__`` so frontends keep
    typed data. Falls back to ``str(value)`` only when nothing more
    specific applies. Sets become sorted lists; bytes become a UTF-8 string
    (with ``replace`` errors) so binary payloads don't crash the stream.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime | date):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, set | frozenset):
            return sorted(o, key=repr)
        if isinstance(o, bytes | bytearray):
            return o.decode("utf-8", errors="replace")
        if isinstance(o, Enum):
            return o.value
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        # Pydantic v2 / v1 — try without importing the dependency.
        model_dump = getattr(o, "model_dump", None)
        if callable(model_dump):
            try:
                return model_dump()
            except Exception:  # noqa: BLE001
                pass
        dict_method = getattr(o, "dict", None)
        if callable(dict_method) and not isinstance(o, type):
            try:
                return dict_method()
            except Exception:  # noqa: BLE001
                pass
        return str(o)


def safe_json_dumps(payload: dict[str, Any]) -> str:
    """Serialize payloads without breaking the SSE stream.

    Best-effort. If the payload itself is fundamentally unserializable
    (for example, contains circular references the encoder cannot break),
    returns a minimal error envelope. Use :func:`build_safe_event_payload`
    when you need to preserve the originating event's id/type so frontend
    cursors and dispatchers stay correct.
    """
    try:
        return json.dumps(payload, cls=_BridgeJSONEncoder)
    except Exception:
        logger.exception("Failed to serialize SSE payload")
        return json.dumps({"event": "error", "message": "Failed to serialize event payload"})


def build_safe_event_payload(
    payload: dict[str, Any],
    *,
    event_id: str,
    event_type: str,
    timestamp: str,
) -> str:
    """Serialize a UIEvent payload, preserving id/type on fallback.

    Frontends use the SSE event id for ``Last-Event-ID`` reconnection and
    the type for dispatch. When the data field is unserializable we still
    emit a stable envelope carrying those identifiers so:

    * ``Last-Event-ID`` resumption keeps working,
    * the frontend can route the event (or surface a typed error),
    * and a structured ``serialization_error`` field tells operators which
      payload misbehaved without leaking its contents to the wire.
    """
    try:
        return json.dumps(payload, cls=_BridgeJSONEncoder)
    except Exception as exc:
        logger.exception("Failed to serialize SSE payload for event %s (%s)", event_id, event_type)
        fallback = {
            "id": event_id,
            "type": event_type,
            "timestamp": timestamp,
            "data": {
                "serialization_error": True,
                "error_class": type(exc).__name__,
                "message": "Event payload could not be serialized for transport.",
            },
        }
        try:
            return json.dumps(fallback)
        except Exception:
            # Should be impossible, but never break the SSE stream.
            return json.dumps(
                {
                    "id": event_id,
                    "type": event_type,
                    "timestamp": timestamp,
                    "data": {"serialization_error": True},
                }
            )


__all__ = [
    "build_safe_event_payload",
    "logger",
    "safe_json_dumps",
]
