"""Server-Sent Events (SSE) client implementation.

This module provides a production-ready SSE client using urllib for HTTP streaming.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import IO, cast

from maivn_shared import loads

from maivn._internal.core import SSEClient, SSEEvent

# MARK: - StreamingSSEClient


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
        self._timeout = timeout

    # MARK: - Public Methods

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
        from urllib.request import Request, urlopen

        merged_headers: dict[str, str] = {}
        if headers:
            merged_headers = {
                key: value for key, value in headers.items() if key.lower() != "accept"
            }
        merged_headers["Accept"] = "text/event-stream"
        req = Request(url, headers=merged_headers)
        try:
            with cast(IO[bytes], urlopen(req, timeout=self._timeout)) as resp:
                buf = b""
                while True:
                    chunk = resp.readline()
                    if not chunk:
                        raise RuntimeError(
                            "SSE stream closed unexpectedly. The server may have stopped, "
                            "the connection may have been interrupted, "
                            "or the session may have ended."
                        )
                    buf += chunk
                    if self._is_event_complete(buf):
                        yield self._parse_event(buf)
                        buf = b""
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Failed to read SSE event stream. The server may be unreachable "
                "or closed the connection."
            ) from exc

    # MARK: - Private Methods

    def _is_event_complete(self, buf: bytes) -> bool:
        """Check if the buffer contains a complete SSE event.

        Args:
            buf: The accumulated byte buffer

        Returns:
            True if the buffer ends with a double newline delimiter
        """
        return buf.endswith(b"\n\n") or buf.endswith(b"\r\n\r\n")

    def _parse_event(self, buf: bytes) -> SSEEvent:
        """Parse a complete SSE event from the buffer.

        Args:
            buf: The byte buffer containing a complete event

        Returns:
            Parsed SSEEvent object
        """
        lines = buf.decode("utf-8", errors="replace").splitlines()
        event_name = "message"
        data_lines: list[str] = []

        for line in lines:
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())

        data = "\n".join(data_lines) if data_lines else "{}"
        payload = self._parse_payload(data)
        return SSEEvent(name=event_name, payload=payload)

    def _parse_payload(self, data: str) -> dict:
        """Parse the event data payload as JSON.

        Args:
            data: The raw data string from the event

        Returns:
            Parsed dictionary or fallback with raw data
        """
        if not data:
            return {}
        try:
            return loads(data)
        except Exception:
            return {"raw": data}


__all__ = ["StreamingSSEClient"]
