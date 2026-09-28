"""Unit tests for copied-and-adapted SSE frame parsing."""

from __future__ import annotations

from maivn._internal.transport.sse_client import parse_sse_frame

_FINAL_POSITION = 7
_UPDATE_POSITION = 8


def test_parse_sse_frame_reads_id_event_and_json_payload() -> None:
    """A complete SSE frame is parsed into a typed stream event."""
    event = parse_sse_frame(
        f'id: {_FINAL_POSITION}\nevent: final\n'
        'data: {"type":"final","payload":{"ok":true}}\n\n',
    )

    assert event.position == _FINAL_POSITION
    assert event.event_type == 'final'
    assert event.name == 'final'
    assert event.data['type'] == 'final'
    assert event.payload == {'ok': True}


def test_parse_sse_frame_joins_multiline_data() -> None:
    """Multiline SSE data is joined before JSON decoding."""
    event = parse_sse_frame(
        f'id: {_UPDATE_POSITION}\nevent: update\ndata: {{"text":\ndata: "hello"}}\n\n',
    )

    assert event.position == _UPDATE_POSITION
    assert event.event_type == 'update'
    assert event.data == {'text': 'hello'}
    assert event.name == 'update'
    assert event.payload == {'text': 'hello'}
