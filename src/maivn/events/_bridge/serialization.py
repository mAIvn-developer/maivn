"""Serialization helpers for bridge SSE payloads."""

# pyright: strict
from __future__ import annotations

import json
import logging
from dataclasses import Field, asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Protocol, TypeGuard, cast, runtime_checkable
from uuid import UUID

from maivn_shared.infrastructure.logging import LoggerProtocol
from typing_extensions import override

logger: LoggerProtocol = cast(
    LoggerProtocol,
    cast(object, logging.getLogger("maivn.events._bridge")),
)


# MARK: JSON Helpers


@runtime_checkable
class _SupportsModelDump(Protocol):
    def model_dump(self) -> object: ...


@runtime_checkable
class _SupportsDictMethod(Protocol):
    def dict(self) -> object: ...


class _DataclassInstance(Protocol):
    __dataclass_fields__: ClassVar[dict[str, Field[object]]]


def _is_dataclass_instance(value: object) -> TypeGuard[_DataclassInstance]:
    return not isinstance(value, type) and hasattr(type(value), "__dataclass_fields__")


class _BridgeJSONEncoder(json.JSONEncoder):
    """Tolerant encoder for common Python value types we cannot leak to ``str``.

    Prefers structured representations over ``__str__`` so frontends keep
    typed data. Falls back to ``str(value)`` only when nothing more
    specific applies. Sets become sorted lists; bytes become a UTF-8 string
    (with ``replace`` errors) so binary payloads don't crash the stream.
    """

    @override
    def default(self, o: object) -> object:
        if isinstance(o, datetime | date):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, set | frozenset):
            values = cast(set[object] | frozenset[object], o)
            return sorted(values, key=repr)
        if isinstance(o, bytes | bytearray):
            return o.decode("utf-8", errors="replace")
        if isinstance(o, Enum):
            return cast(object, o.value)
        if _is_dataclass_instance(o):
            return cast(object, asdict(o))
        # Pydantic v2 / v1 - try without importing the dependency.
        if isinstance(o, _SupportsModelDump):
            try:
                return o.model_dump()
            except Exception:  # noqa: BLE001 - model dumps are best-effort fallbacks.
                pass
        if isinstance(o, _SupportsDictMethod) and not isinstance(o, type):
            try:
                return o.dict()
            except Exception:  # noqa: BLE001 - legacy dict() dumps are best-effort fallbacks.
                pass
        return str(o)


def safe_json_dumps(payload: dict[str, object]) -> str:
    """Serialize payloads without breaking the SSE stream.

    Best-effort. If the payload itself is fundamentally unserializable
    (for example, contains circular references the encoder cannot break),
    returns a minimal error envelope. Use :func:`build_safe_event_payload`
    when you need to preserve the originating event's id/type so frontend
    cursors and dispatchers stay correct.
    """
    try:
        return json.dumps(payload, cls=_BridgeJSONEncoder)
    except Exception:  # noqa: BLE001 - serialization must degrade to a stable error envelope.
        logger.exception("Failed to serialize SSE payload")
        return json.dumps({"event": "error", "message": "Failed to serialize event payload"})


def build_safe_event_payload(
    payload: dict[str, object],
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
    except Exception as exc:  # noqa: BLE001 - never let one bad event break the SSE stream.
        logger.exception(f"Failed to serialize SSE payload for event {event_id} ({event_type})")
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
        except Exception:  # noqa: BLE001 - the minimal fallback should be impossible to reject.
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
