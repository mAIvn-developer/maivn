"""Streaming and lifecycle helpers for EventBridge."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .serialization import logger

if TYPE_CHECKING:
    from .bridge import EventBridge
    from .ui_event import UIEvent


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
) -> AsyncGenerator[dict[str, Any], None]:
    # Snapshot up front so concurrent emits during replay don't reorder
    # what the consumer sees. asyncio.Queue.put_nowait into a non-empty
    # queue is sync, so this snapshot is consistent under the single-
    # threaded event-loop model.
    history: list[UIEvent] = list(bridge._event_history)
    if not history:
        return

    replay_start = 0
    cursor_resolved = last_event_id is None
    if last_event_id is not None:
        for index, event in enumerate(history):
            if event.id == last_event_id:
                replay_start = index + 1
                cursor_resolved = True
                break
        if not cursor_resolved:
            evictions = getattr(bridge, "_history_evictions", 0)
            if evictions:
                logger.warning(
                    "Replay cursor %s unknown for session %s; %d events have aged out "
                    "of the %d-event history buffer. Replaying full buffer; client may "
                    "see duplicates.",
                    last_event_id,
                    bridge.session_id,
                    evictions,
                    bridge._max_history,
                )
            else:
                logger.info(
                    "Replay cursor %s not in history for session %s "
                    "(possible new turn); replaying full buffer",
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


def _build_keepalive_frame() -> dict[str, Any]:
    """Yield an SSE comment frame as keep-alive.

    Browsers ignore comment frames entirely, so frontends do not need to
    subscribe to or filter a heartbeat event type. Matches sse-starlette's
    built-in ping shape.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    return {"comment": f"keepalive {timestamp}"}


async def generate_sse_events(
    bridge: EventBridge,
    *,
    last_event_id: str | None = None,
    heartbeat_interval: float | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield SSE-shaped dicts for the lifetime of one client connection.

    The generator is structured so that ``GeneratorExit`` /
    ``CancelledError`` (raised by Starlette / sse-starlette when the client
    disconnects) cleanly tears down the read of ``bridge._queue`` instead
    of leaving a coroutine pending on ``queue.get()`` forever.

    Pass ``heartbeat_interval`` to override the bridge default for a
    specific stream (useful when the client lives behind a proxy with a
    short idle timeout).
    """
    interval = heartbeat_interval if heartbeat_interval is not None else bridge._heartbeat_interval
    if interval <= 0:
        raise ValueError("heartbeat_interval must be > 0")

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
                    timeout=interval,
                )
                if event.id in replayed_ids:
                    continue
                yield event.to_sse()
                if event.type in bridge.TERMINAL_EVENTS:
                    bridge._closed = True
                    break
            except TimeoutError:
                yield _build_keepalive_frame()

    except (asyncio.CancelledError, GeneratorExit):
        logger.debug("SSE stream cancelled for session %s", bridge.session_id)
        # Re-raise GeneratorExit per PEP 525 so the runtime knows the
        # generator finalized cleanly. CancelledError likewise should not
        # be swallowed silently in newer Python.
        raise


# MARK: Lifecycle


def reopen_bridge(bridge: EventBridge) -> None:
    bridge._closed = False
    bridge._event_history.clear()
    while not bridge._queue.empty():
        try:
            bridge._queue.get_nowait()
        except asyncio.QueueEmpty:
            break
