"""Networking infrastructure.
Provides the SSE client implementation for orchestrator communication.
"""

# pyright: strict
from __future__ import annotations

from .sse_client import StreamingSSEClient

# MARK: Exports

__all__ = [
    "StreamingSSEClient",
]
