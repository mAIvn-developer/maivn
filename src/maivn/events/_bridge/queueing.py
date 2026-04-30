"""Queue backpressure helpers for EventBridge."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .serialization import logger

if TYPE_CHECKING:
    from .bridge import EventBridge
    from .ui_event import UIEvent


# MARK: Queueing


async def enqueue_event(bridge: EventBridge, event: UIEvent) -> None:
    """Place an event on the live queue, applying the bridge backpressure policy."""
    if bridge._queue_maxsize == 0:
        bridge._queue.put_nowait(event)
        return

    if bridge._backpressure == "block":
        await bridge._queue.put(event)
        return

    if bridge._backpressure == "drop_newest":
        try:
            bridge._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Dropping newest event for session %s (queue full, type=%s)",
                bridge.session_id,
                event.type,
            )
        return

    # drop_oldest
    while True:
        try:
            bridge._queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            try:
                dropped = bridge._queue.get_nowait()
            except asyncio.QueueEmpty:
                # Defensive guard; full and empty should not happen together.
                return
            logger.warning(
                "Dropping oldest event for session %s (queue full, type=%s)",
                bridge.session_id,
                dropped.type,
            )


__all__ = ["enqueue_event"]
