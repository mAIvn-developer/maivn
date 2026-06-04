# pyright: strict
from __future__ import annotations

import urllib.request
from types import TracebackType
from typing import final

import pytest

from maivn._internal.adapters.networking import sse_client as sse_client_module
from maivn._internal.adapters.networking.sse_client import StreamingSSEClient
from maivn._internal.core import SSEEvent


@final
class _FakeResponse:
    _lines: list[bytes]

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def test_iter_events_parses_event(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b"event: update\n",
        b'data: {"ok": true}\n',
        b"\n",
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        del req, timeout
        return _FakeResponse(lines)

    monkeypatch.setattr(sse_client_module, "urlopen", fake_urlopen)

    client = StreamingSSEClient(timeout=1.0)
    event = next(client.iter_events("http://example.com/stream"))

    assert event.name == "update"
    assert event.payload == {"ok": True}


def test_iter_events_handles_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        b"data: not-json\n",
        b"\n",
    ]

    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        del req, timeout
        return _FakeResponse(lines)

    monkeypatch.setattr(sse_client_module, "urlopen", fake_urlopen)

    client = StreamingSSEClient(timeout=1.0)
    event = next(client.iter_events("http://example.com/stream"))

    assert event.payload == {"raw": "not-json"}


def _parse_event(client: StreamingSSEClient, buf: bytes) -> SSEEvent:
    """Read the protected ``_parse_event`` via ``getattr`` to bypass
    ``reportPrivateUsage`` without adding a pyright ignore."""
    parser = client.parse_event
    return parser(buf)


def test_parse_event_preserves_sse_data_with_vertical_tab() -> None:
    client = StreamingSSEClient(timeout=1.0)

    event = _parse_event(client, b"data: hello\vworld\n\n")

    assert event.payload == {"raw": "hello\vworld"}


def test_iter_events_raises_when_stream_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        del req, timeout
        return _FakeResponse([])

    monkeypatch.setattr(sse_client_module, "urlopen", fake_urlopen)

    client = StreamingSSEClient(timeout=1.0)
    with pytest.raises(RuntimeError, match="SSE stream closed unexpectedly"):
        _ = next(client.iter_events("http://example.com/stream"))


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
        _ = next(client.iter_events(url))
