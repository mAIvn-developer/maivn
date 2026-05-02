from __future__ import annotations

import urllib.request

import pytest

from maivn._internal.adapters.networking.sse_client import StreamingSSEClient


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_iter_events_parses_event(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b"event: update\n",
        b'data: {"ok": true}\n',
        b"\n",
    ]

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(lines)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = StreamingSSEClient(timeout=1.0)
    event = next(client.iter_events("http://example.com/stream"))

    assert event.name == "update"
    assert event.payload == {"ok": True}


def test_iter_events_handles_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b"data: not-json\n",
        b"\n",
    ]

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(lines))

    client = StreamingSSEClient(timeout=1.0)
    event = next(client.iter_events("http://example.com/stream"))

    assert event.payload == {"raw": "not-json"}


def test_iter_events_raises_when_stream_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse([]))

    client = StreamingSSEClient(timeout=1.0)
    with pytest.raises(RuntimeError, match="Failed to read SSE event stream"):
        next(client.iter_events("http://example.com/stream"))


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/session-events",
        "ftp://example.com/stream",
        "/relative/events",
    ],
)
def test_iter_events_rejects_non_http_stream_urls(url: str) -> None:
    client = StreamingSSEClient(timeout=1.0)
    with pytest.raises(ValueError, match="absolute http:// or https://"):
        next(client.iter_events(url))
