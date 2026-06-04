"""Queue backpressure helpers for EventBridge."""

# pyright: strict
from __future__ import annotations

import asyncio

from .serialization import logger
from .ui_event import UIEvent

# MARK: Queueing


async def enqueue_event(
    *,
    queue: asyncio.Queue[UIEvent],
    queue_maxsize: int,
    backpressure: str,
    session_id: str,
    event: UIEvent,
) -> None:
    """Place an event on the live queue, applying the bridge backpressure policy."""
    if queue_maxsize == 0:
        queue.put_nowait(event)
        return

    if backpressure == "block":
        await queue.put(event)
        return

    if backpressure == "drop_newest":
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Dropping newest event for session %s (queue full, type=%s)",
                session_id,
                event.type,
            )
        return

    # drop_oldest
    while True:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            try:
                dropped = queue.get_nowait()
            except asyncio.QueueEmpty:
                # Defensive guard; full and empty should not happen together.
                return
            logger.warning(
                "Dropping oldest event for session %s (queue full, type=%s)",
                session_id,
                dropped.type,
            )


__all__ = ["enqueue_event"]
