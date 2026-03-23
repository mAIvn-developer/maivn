"""Server-sent events client interface.
Defines the protocol for streaming SSE events from a remote endpoint.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from maivn._internal.core.entities.sse_event import SSEEvent

# MARK: - SSE Client Interface


class SSEClient(Protocol):
    """Protocol for streaming server-sent events."""

    def iter_events(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[SSEEvent]:
        """Yield SSE events from the provided URL.

        Args:
            url: The SSE endpoint URL to connect to.
            headers: Optional HTTP headers to include with the SSE request.

        Yields:
            SSEEvent objects as they arrive from the stream.
        """
        ...
