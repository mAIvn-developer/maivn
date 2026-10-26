"""An event whose identifiers hold a lone surrogate still becomes a frame (U05-D21)."""

from __future__ import annotations

import json

from maivn.events import UIEvent


def test_a_lone_surrogate_in_the_event_type_is_escaped_not_raised() -> None:
    """The frame keeps the stream alive and shows the bad code point escaped."""
    event = UIEvent(type='bad\ud800', data={'k': 'v'}, id='evt-1', timestamp='2026-10-10T00:00:00Z')

    frame = event.to_sse()

    assert frame['event'] == 'bad\\ud800'
    assert frame['id'] == 'evt-1'
    assert json.loads(str(frame['data'])) == {
        'id': 'evt-1',
        'type': 'bad\\ud800',
        'timestamp': '2026-10-10T00:00:00Z',
        'data': {
            'serialization_error': True,
            'error_class': 'TypeError',
            'message': 'Event payload could not be serialized for transport.',
        },
    }
    assert str(frame['data']).encode('utf-8')


def test_a_lone_surrogate_in_the_event_id_is_escaped_too() -> None:
    """The id line, used for Last-Event-ID resumption, stays encodable."""
    event = UIEvent(type='final', data={}, id='evt-\udfff', timestamp='t')

    frame = event.to_sse()

    assert frame['id'] == 'evt-\\udfff'
    assert json.loads(str(frame['data']))['id'] == 'evt-\\udfff'


def test_ordinary_events_are_unchanged() -> None:
    """Valid text, including non-ASCII, is sent as it is."""
    event = UIEvent(type='status_message', data={'text': 'héllo ✓'}, id='evt-2', timestamp='t')

    frame = event.to_sse()

    assert frame['event'] == 'status_message'
    assert json.loads(str(frame['data'])) == {
        'id': 'evt-2',
        'type': 'status_message',
        'data': {'text': 'héllo ✓'},
        'timestamp': 't',
    }
