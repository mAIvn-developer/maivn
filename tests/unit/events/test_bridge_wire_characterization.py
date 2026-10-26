"""Characterization of the bridge wire format: SSE JSON serialization and schema guardrails.

``safe_json_dumps`` / ``build_safe_event_payload`` must always produce valid
JSON (or a stable error envelope) and the schema guardrail must report the
exact problems it found, in ``warn`` and ``strict`` modes, directly and through
``EventBridge``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import decimal
import enum
import json
import logging
import math
import re
import uuid
from typing import cast

import pytest
from pydantic import BaseModel

from maivn.events import EventBridge, UIEvent
from maivn.events._bridge import (
    EventSchemaError,
    build_safe_event_payload,
    safe_json_dumps,
    validate_event,
)

TOO_BIG = 2**70


class _Color(enum.Enum):
    RED = 'red'


@dataclasses.dataclass
class _Record:
    count: int
    when: datetime.date


class _Model(BaseModel):
    x: int = 1


class _LegacyModel:
    def model_dump(self) -> object:
        """Fail like a half-migrated model."""
        raise RuntimeError

    def dict(self) -> object:
        """Return the legacy pydantic v1 shape."""
        return {'legacy': True}


class _BrokenModel:
    def model_dump(self) -> object:
        """Fail like a broken model."""
        raise RuntimeError

    def dict(self) -> object:
        """Fail like a broken legacy model."""
        raise RuntimeError

    def __str__(self) -> str:
        """Return text the serializer never emits."""
        return 'broken-model'


class _Plain:
    def __str__(self) -> str:
        """Return text the serializer never emits."""
        return 'plain-text'


# MARK: JSON Serialization


def test_safe_json_dumps_encodes_rich_python_values() -> None:
    """Datetimes, decimals, sets, bytes, dataclasses, models and enums become plain JSON.

    Anything else becomes its type name, never its ``str()`` (U05-D22).
    """
    payload: dict[str, object] = {
        'dt': datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
        'date': datetime.date(2026, 1, 2),
        'decimal': decimal.Decimal('1.10'),
        'set': {64, 1, 33},
        'frozen': frozenset({'b', 'a'}),
        'bytes': b'ok\xff',
        'bytearray': bytearray(b'hi'),
        'record': _Record(1, datetime.date(2026, 1, 2)),
        'model': _Model(),
        'legacy': _LegacyModel(),
        'broken': _BrokenModel(),
        'nan': math.nan,
        'inf': -math.inf,
        'uuid': uuid.UUID('12345678-1234-5678-1234-567812345678'),
        'enum': _Color.RED,
        'plain': _Plain(),
    }

    assert json.loads(safe_json_dumps(payload)) == {
        'dt': '2026-01-02T03:04:05+00:00',
        'date': '2026-01-02',
        'decimal': '1.10',
        'set': [1, 33, 64],
        'frozen': ['a', 'b'],
        'bytes': 'ok�',
        'bytearray': 'hi',
        'record': {'count': 1, 'when': '2026-01-02'},
        'model': {'x': 1},
        'legacy': {'legacy': True},
        'broken': '_BrokenModel',
        'nan': None,
        'inf': None,
        'uuid': '12345678-1234-5678-1234-567812345678',
        'enum': 'red',
        'plain': '_Plain',
    }


def test_safe_json_dumps_falls_back_to_an_error_envelope(caplog: pytest.LogCaptureFixture) -> None:
    """An unencodable payload yields a fixed error document and an exception log."""
    with caplog.at_level(logging.ERROR):
        result = safe_json_dumps({'big': TOO_BIG})

    assert json.loads(result) == {'event': 'error', 'message': 'Failed to serialize event payload'}
    assert [record.getMessage() for record in caplog.records] == ['Failed to serialize SSE payload']
    assert caplog.records[0].exc_info is not None


def test_event_payload_fallback_keeps_event_identity(caplog: pytest.LogCaptureFixture) -> None:
    """The per-event fallback preserves id, type and timestamp and names the error class."""
    with caplog.at_level(logging.ERROR):
        result = build_safe_event_payload(
            {'big': TOO_BIG}, event_id='e1', event_type='final', timestamp='ts'
        )

    assert json.loads(result) == {
        'id': 'e1',
        'type': 'final',
        'timestamp': 'ts',
        'data': {
            'serialization_error': True,
            'error_class': 'TypeError',
            'message': 'Event payload could not be serialized for transport.',
        },
    }
    assert [record.getMessage() for record in caplog.records] == [
        'Failed to serialize SSE payload for event e1 (final)'
    ]


def test_unencodable_live_event_still_reaches_the_client_as_an_envelope() -> None:
    """A bridge event that cannot be encoded keeps its SSE id and type."""
    bridge = EventBridge('wire', schema_validation='off')
    asyncio.run(bridge.emit('custom', {'big': TOO_BIG}))
    event = bridge.stream_queue_get_nowait()

    frame = event.to_sse()

    assert frame['event'] == 'custom'
    assert frame['id'] == event.id
    envelope = cast('dict[str, object]', json.loads(cast('str', frame['data'])))
    assert envelope['id'] == event.id
    assert envelope['type'] == 'custom'
    assert envelope['timestamp'] == event.timestamp
    assert cast('dict[str, object]', envelope['data'])['serialization_error'] is True


# MARK: Schema Guardrail


@pytest.mark.parametrize(
    ('event_type', 'data', 'problem'),
    [
        (1, {}, 'event_type must be str, got int'),
        ('', {}, 'event_type must be non-empty'),
        (' ', [], 'event_type must be non-empty; data must be a mapping, got list'),
        ('custom', 5, 'data must be a mapping, got int'),
        ('final', {}, "missing required fields ['response']"),
        (
            'tool_event',
            {'tool_id': 't', 'tool_name': ''},
            "missing required fields ['tool_name', 'status']",
        ),
        (
            'custom',
            {'a': [{'constructor': 1}], '__class__': {'__proto__': 2}},
            'reserved field names present at '
            '$.a[0].constructor, $.__class__, $.__class__.__proto__',
        ),
    ],
)
def test_strict_validation_reports_every_problem(
    event_type: object,
    data: object,
    problem: str,
) -> None:
    """Strict mode raises ``EventSchemaError`` naming the event and its problems."""
    message = f'Event {event_type!r} failed schema validation: {problem}'

    with pytest.raises(EventSchemaError, match=f'^{re.escape(message)}$'):
        validate_event(event_type, data, mode='strict')


def test_valid_events_and_off_mode_pass_silently(caplog: pytest.LogCaptureFixture) -> None:
    """Valid payloads, unknown-but-clean types and ``off`` mode raise nothing and log nothing."""
    cyclic: dict[str, object] = {'response': 'ok'}
    cyclic['self'] = cyclic

    with caplog.at_level(logging.DEBUG):
        validate_event('final', cyclic, mode='strict')
        validate_event('custom', {'anything': [1, {'nested': True}]}, mode='strict')
        validate_event(1, None, mode='off')

    assert caplog.records == []


def test_warn_mode_logs_the_same_message(caplog: pytest.LogCaptureFixture) -> None:
    """Warn mode logs the problem instead of raising."""
    with caplog.at_level(logging.WARNING):
        validate_event('error', {'error': ''}, mode='warn')

    assert [record.getMessage() for record in caplog.records] == [
        "Event 'error' failed schema validation: missing required fields ['error']"
    ]


def test_unknown_validation_mode_is_rejected() -> None:
    """Only off, warn and strict are accepted."""
    with pytest.raises(
        ValueError, match=re.escape("mode must be one of ['off', 'strict', 'warn'], got 'loud'")
    ):
        validate_event('final', {'response': 'x'}, mode='loud')  # pyright: ignore[reportArgumentType]


def test_strict_bridge_refuses_invalid_events_before_storing_them() -> None:
    """A strict bridge raises and leaves history and the queue untouched."""
    bridge = EventBridge('wire', schema_validation='strict')

    with pytest.raises(
        EventSchemaError,
        match=re.escape(
            "Event 'status_message' failed schema validation: missing required fields ['message']"
        ),
    ):
        asyncio.run(bridge.emit('status_message', {}))

    assert bridge.get_history() == []
    assert bridge.stream_queue_empty() is True


def test_warning_bridge_logs_and_still_delivers(caplog: pytest.LogCaptureFixture) -> None:
    """The default ``warn`` bridge logs reserved names and still emits the event."""
    bridge = EventBridge('wire')

    with caplog.at_level(logging.WARNING):
        asyncio.run(bridge.emit('custom', {'__proto__': {'polluted': True}}))

    assert [record.getMessage() for record in caplog.records] == [
        "Event 'custom' failed schema validation: reserved field names present at $.__proto__"
    ]
    assert bridge.get_history()[0]['data'] == {'__proto__': {'polluted': True}}


def test_ui_event_keeps_explicit_identity_and_generates_missing_ones() -> None:
    """Explicit ids and timestamps are kept; missing ones are generated."""
    explicit = UIEvent(type='final', data={'a': 1}, id='evt-1', timestamp='2026-01-01T00:00:00Z')
    generated = UIEvent(type='final', data={})

    assert explicit.to_dict() == {
        'id': 'evt-1',
        'type': 'final',
        'data': {'a': 1},
        'timestamp': '2026-01-01T00:00:00Z',
    }
    assert explicit.to_sse()['id'] == 'evt-1'
    assert uuid.UUID(generated.id).version == 4  # noqa: PLR2004
    assert datetime.datetime.fromisoformat(generated.timestamp).tzinfo is not None
