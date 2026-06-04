"""Server-Sent Events (SSE) client implementation.

This module provides an urllib SSE client for HTTP streaming.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Iterator
from typing import IO, Final, cast
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from maivn_shared import loads
from pydantic import JsonValue
from typing_extensions import override

from ...core import SSEClient, SSEEvent

# MARK: Constants

ACCEPT_HEADER: Final = "Accept"
ACCEPT_HEADER_LOWER: Final = ACCEPT_HEADER.lower()
SSE_CONTENT_TYPE: Final = "text/event-stream"
ALLOWED_STREAM_SCHEMES: Final = frozenset({"http", "https"})
DEFAULT_EVENT_NAME: Final = "message"
EVENT_FIELD_PREFIX: Final = "event:"
DATA_FIELD_PREFIX: Final = "data:"
EMPTY_JSON_OBJECT: Final = "{}"
RAW_PAYLOAD_KEY: Final = "raw"


# MARK: Types

JsonObject = dict[str, JsonValue]


# MARK: StreamingSSEClient


class StreamingSSEClient(SSEClient):
    """urllib-based SSE client for production use.

    This client implements the SSE protocol for consuming server-sent event streams
    over HTTP. It handles event parsing and yields structured SSEEvent objects.
    """

    # MARK: - Initialization

    def __init__(self, *, timeout: float = 600.0) -> None:
        """Initialize the SSE client.

        Args:
            timeout: Request timeout in seconds (default: 600.0)
        """
        self._timeout: float = timeout

    # MARK: - Public Methods

    @override
    def iter_events(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[SSEEvent]:
        """Iterate over server-sent events from the given URL.

        Args:
            url: The SSE endpoint URL to connect to
            headers: Optional HTTP headers to include with the SSE request

        Yields:
            SSEEvent objects parsed from the event stream

        Raises:
            URLError: If the connection fails
            TimeoutError: If the request times out
        """
        self._validate_stream_url(url)
        merged_headers: dict[str, str] = {}
        if headers:
            merged_headers = {
                key: value for key, value in headers.items() if key.lower() != ACCEPT_HEADER_LOWER
            }
        merged_headers[ACCEPT_HEADER] = SSE_CONTENT_TYPE
        req = Request(url, headers=merged_headers)
        try:
            # _validate_stream_url rejects file: and custom schemes before urlopen.
            with cast(
                IO[bytes],
                urlopen(req, timeout=self._timeout),  # noqa: S310  # nosec B310
            ) as resp:
                chunks: list[bytes] = []
                event_tail = b""
                while True:
                    chunk = resp.readline()
                    if not chunk:
                        raise RuntimeError(
                            "SSE stream closed unexpectedly. The server may have stopped, "
                            + "the connection may have been interrupted, "
                            + "or the session may have ended."
                        )
                    chunks.append(chunk)
                    event_tail = (event_tail + chunk)[-4:]
                    if self._is_event_complete(event_tail):
                        yield self._parse_event(b"".join(chunks))
                        chunks.clear()
                        event_tail = b""
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap stream failures in RuntimeError.
            raise RuntimeError(
                "Failed to read SSE event stream. The server may be unreachable "
                + "or closed the connection."
            ) from exc

    # MARK: - Private Methods

    @staticmethod
    def _validate_stream_url(url: str) -> None:
        """Reject local-file and custom-scheme URLs before urllib opens them."""
        parsed = urlsplit(url)
        if parsed.scheme not in ALLOWED_STREAM_SCHEMES or not parsed.netloc:
            raise ValueError("SSE stream URL must be an absolute http:// or https:// URL")

    def _is_event_complete(self, buf: bytes) -> bool:
        """Check if the buffer contains a complete SSE event.

        Args:
            buf: The accumulated byte buffer

        Returns:
            True if the buffer ends with a double newline delimiter
        """
        normalized = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        return normalized.endswith(b"\n\n")

    def _parse_event(self, buf: bytes) -> SSEEvent:
        """Parse a complete SSE event from the buffer.

        Args:
            buf: The byte buffer containing a complete event

        Returns:
            Parsed SSEEvent object
        """
        text = buf.decode("utf-8", errors="replace")
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        event_name = DEFAULT_EVENT_NAME
        data_lines: list[str] = []

        for line in lines:
            if line.startswith(EVENT_FIELD_PREFIX):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith(DATA_FIELD_PREFIX):
                data_lines.append(line.split(":", 1)[1].strip())

        data = "\n".join(data_lines) if data_lines else EMPTY_JSON_OBJECT
        payload = self._parse_payload(data)
        return SSEEvent(name=event_name, payload=payload)

    def parse_event(self, buf: bytes) -> SSEEvent:
        """Parse a complete SSE event from the buffer."""
        return self._parse_event(buf)

    def _parse_payload(self, data: str) -> JsonValue:
        """Parse the event data payload as JSON.

        Args:
            data: The raw data string from the event

        Returns:
            Parsed JSON value or fallback with raw data.
        """
        if not data:
            empty_payload: JsonObject = {}
            return empty_payload
        try:
            return loads(data)
        except Exception:  # noqa: BLE001 - invalid JSON falls back to raw SSE data.
            raw_payload: JsonObject = {RAW_PAYLOAD_KEY: data}
            return raw_payload


__all__ = ["StreamingSSEClient"]
