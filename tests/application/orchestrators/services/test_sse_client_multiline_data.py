from __future__ import annotations

from maivn._internal.adapters.networking.sse_client import StreamingSSEClient


def test_parse_event_concatenates_multiple_data_lines_before_json_load() -> None:
    client = StreamingSSEClient()

    buf = b'event: update\ndata: {"a": 1,\ndata: "b": 2}\n\n'

    event = client._parse_event(buf)

    assert event.name == "update"
    assert event.payload == {"a": 1, "b": 2}
