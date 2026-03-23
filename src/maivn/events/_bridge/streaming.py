"""Streaming and lifecycle helpers for EventBridge."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .serialization import logger, safe_json_dumps

if TYPE_CHECKING:
    from .bridge import EventBridge, UIEvent


# MARK: History Replay


def _drain_replayed_events(
    bridge: EventBridge,
    replayed_ids: set[str],
) -> None:
    drained = 0
    pending_live: list[UIEvent] = []
    while not bridge._queue.empty():
        try:
            queued = bridge._queue.get_nowait()
            if queued.id in replayed_ids:
                drained += 1
                continue
            pending_live.append(queued)
        except asyncio.QueueEmpty:
            break

    for queued in pending_live:
        bridge._queue.put_nowait(queued)

    if drained:
        logger.debug(
            "Drained %d already-replayed events from queue for session %s",
            drained,
            bridge.session_id,
        )


async def _replay_history(
    bridge: EventBridge,
    *,
    last_event_id: str | None,
    replayed_ids: set[str],
) -> AsyncIterator[dict[str, Any]]:
    if not bridge._event_history:
        return

    history = list(bridge._event_history)
    replay_start = 0
    if last_event_id is not None:
        for index, event in enumerate(history):
            if event.id == last_event_id:
                replay_start = index + 1
                break
        else:
            logger.debug(
                "Replay cursor %s not found for session %s; replaying full buffered history",
                last_event_id,
                bridge.session_id,
            )

    logger.debug(
        "Replaying %d buffered events for session %s (last_event_id=%s)",
        len(history),
        bridge.session_id,
        last_event_id,
    )

    for event in history:
        replayed_ids.add(event.id)

    for event in history[replay_start:]:
        yield event.to_sse()
        if event.type in bridge.TERMINAL_EVENTS:
            bridge._closed = True
            return

    if replay_start:
        logger.debug(
            "Skipped %d already-seen events for session %s",
            replay_start,
            bridge.session_id,
        )

    _drain_replayed_events(bridge, replayed_ids)


# MARK: Live Streaming


async def generate_sse_events(
    bridge: EventBridge,
    *,
    last_event_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    try:
        replayed_ids: set[str] = set()
        async for event in _replay_history(
            bridge,
            last_event_id=last_event_id,
            replayed_ids=replayed_ids,
        ):
            yield event
            if bridge._closed:
                return

        while not bridge._closed:
            try:
                event = await asyncio.wait_for(
                    bridge._queue.get(),
                    timeout=bridge._heartbeat_interval,
                )
                if event.id in replayed_ids:
                    continue
                yield event.to_sse()
                if event.type in bridge.TERMINAL_EVENTS:
                    bridge._closed = True
                    break
            except TimeoutError:
                yield {
                    "event": "heartbeat",
                    "data": safe_json_dumps({"timestamp": datetime.now(UTC).isoformat()}),
                }

    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled for session %s", bridge.session_id)
        return


# MARK: Lifecycle


def reopen_bridge(bridge: EventBridge) -> None:
    bridge._closed = False
    bridge._event_history.clear()
    while not bridge._queue.empty():
        try:
            bridge._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
