# pyright: strict
from __future__ import annotations

from maivn._internal.adapters.networking.sse_client import StreamingSSEClient
from maivn._internal.core.entities.sse_event import SSEEvent


def _parse_event(client: StreamingSSEClient, buf: bytes) -> SSEEvent:
    """Expose the protected ``_parse_event`` method as a typed callable for tests."""
    return client.parse_event(buf)


def test_parse_event_concatenates_multiple_data_lines_before_json_load() -> None:
    client = StreamingSSEClient()

    buf = b'event: update\ndata: {"a": 1,\ndata: "b": 2}\n\n'

    event = _parse_event(client, buf)

    assert event.name == "update"
    assert event.payload == {"a": 1, "b": 2}
