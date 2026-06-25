# pyright: strict
"""Tests for bridge SSE payload serialization (orjson-backed)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from maivn.events._bridge.serialization import (
    build_safe_event_payload,
    safe_json_dumps,
)

# MARK: - New behavior: valid JSON on the wire (RED first)


def test_nan_serializes_to_valid_json() -> None:
    """float('nan') must not leak the invalid bare ``NaN`` token onto the SSE wire."""
    out = safe_json_dumps({"value": float("nan")})
    assert "NaN" not in out
    assert json.loads(out)["value"] is None


def test_infinity_serializes_to_valid_json() -> None:
    """Infinity must serialize to valid JSON (``null``), not ``Infinity``/``-Infinity``."""
    out = safe_json_dumps({"a": float("inf"), "b": float("-inf")})
    assert "Infinity" not in out
    parsed = json.loads(out)
    assert parsed == {"a": None, "b": None}


# MARK: - Characterization: preserved type handling


def test_datetime_uses_isoformat() -> None:
    dt = datetime(2026, 6, 22, 12, 30, 0, tzinfo=timezone.utc)
    out = safe_json_dumps({"ts": dt})
    assert json.loads(out)["ts"] == dt.isoformat()


def test_uuid_serializes_to_str() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    out = safe_json_dumps({"id": uid})
    assert json.loads(out)["id"] == str(uid)


def test_decimal_serializes_to_string_not_float() -> None:
    out = safe_json_dumps({"amount": Decimal("10.50")})
    parsed = json.loads(out)
    assert parsed["amount"] == "10.50"
    assert isinstance(parsed["amount"], str)


def test_set_serializes_to_sorted_list() -> None:
    out = safe_json_dumps({"items": {"b", "a", "c"}})
    assert json.loads(out)["items"] == ["a", "b", "c"]


def test_bytes_decode_to_utf8_string() -> None:
    out = safe_json_dumps({"blob": b"hello"})
    assert json.loads(out)["blob"] == "hello"


def test_enum_serializes_to_value() -> None:
    class Color(Enum):
        RED = "red"
        BLUE = "blue"

    out = safe_json_dumps({"color": Color.RED})
    assert json.loads(out)["color"] == "red"


def test_dataclass_serializes_to_dict() -> None:
    @dataclasses.dataclass
    class Point:
        x: int
        y: int

    out = safe_json_dumps({"point": Point(1, 2)})
    assert json.loads(out)["point"] == {"x": 1, "y": 2}


def test_pydantic_model_serializes_via_model_dump() -> None:
    class Item(BaseModel):
        name: str
        qty: int

    out = safe_json_dumps({"item": Item(name="widget", qty=3)})
    assert json.loads(out)["item"] == {"name": "widget", "qty": 3}


# MARK: - Fail-closed: never break the SSE stream


def test_circular_reference_degrades_to_error_envelope() -> None:
    payload: dict[str, object] = {}
    payload["self"] = payload  # circular
    out = safe_json_dumps(payload)
    parsed = json.loads(out)
    assert parsed["event"] == "error"


def test_build_safe_event_payload_preserves_id_and_type_on_failure() -> None:
    payload: dict[str, object] = {}
    payload["self"] = payload  # circular -> unserializable
    out = build_safe_event_payload(
        payload,
        event_id="evt-1",
        event_type="agent.thinking",
        timestamp="2026-06-22T00:00:00Z",
    )
    parsed = json.loads(out)
    assert parsed["id"] == "evt-1"
    assert parsed["type"] == "agent.thinking"
    assert parsed["data"]["serialization_error"] is True


def test_normal_payload_round_trips() -> None:
    out = build_safe_event_payload(
        {"id": "e", "type": "t", "data": {"n": 1, "s": "x"}},
        event_id="e",
        event_type="t",
        timestamp="2026-06-22T00:00:00Z",
    )
    assert json.loads(out)["data"] == {"n": 1, "s": "x"}
