"""Streaming and lifecycle helpers for EventBridge."""

# pyright: strict
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable
from datetime import datetime, timezone
from typing import ClassVar, Protocol, cast

from .serialization import logger
from .ui_event import UIEvent


class StreamBridge(Protocol):
    """EventBridge surface required by streaming helpers."""

    session_id: str
    TERMINAL_EVENTS: ClassVar[frozenset[str]]

    @property
    def stream_default_heartbeat_interval(self) -> float: ...

    @property
    def stream_max_history(self) -> int: ...

    @property
    def stream_history_evictions(self) -> int: ...

    @property
    def stream_is_closed(self) -> bool: ...

    def stream_mark_closed(self) -> None: ...

    def stream_history_snapshot(self) -> list[UIEvent]: ...

    def stream_queue_empty(self) -> bool: ...

    def stream_queue_get_nowait(self) -> UIEvent: ...

    def stream_queue_put_nowait(self, event: UIEvent) -> None: ...

    async def stream_queue_get(self) -> UIEvent: ...

    def stream_subscriber_attached(self) -> None: ...

    def stream_subscriber_detached(self) -> None: ...

    def stream_reset_state(self) -> None: ...


# MARK: History Replay


def _drain_replayed_events(
    bridge: StreamBridge,
    replayed_ids: set[str],
) -> None:
    drained = 0
    pending_live: list[UIEvent] = []
    while not bridge.stream_queue_empty():
        try:
            queued = bridge.stream_queue_get_nowait()
            if queued.id in replayed_ids:
                drained += 1
                continue
            pending_live.append(queued)
        except asyncio.QueueEmpty:
            break

    for queued in pending_live:
        bridge.stream_queue_put_nowait(queued)

    if drained:
        logger.debug(
            "Drained %d already-replayed events from queue for session %s",
            drained,
            bridge.session_id,
        )


async def _replay_history(
    bridge: StreamBridge,
    *,
    last_event_id: str | None,
    replayed_ids: set[str],
) -> AsyncGenerator[dict[str, object], None]:
    # Snapshot up front so concurrent emits during replay don't reorder
    # what the consumer sees. asyncio.Queue.put_nowait into a non-empty
    # queue is sync, so this snapshot is consistent under the single-
    # threaded event-loop model.
    history: list[UIEvent] = bridge.stream_history_snapshot()
    if not history:
        return

    replay_start: int = 0
    cursor_resolved = last_event_id is None
    if last_event_id is not None:
        for index in range(len(history)):
            history_event = history[index]
            if history_event.id == last_event_id:
                replay_start = index + 1
                cursor_resolved = True
                break
        if not cursor_resolved:
            evictions = bridge.stream_history_evictions
            if evictions:
                logger.warning(
                    "Replay cursor %s unknown for session %s; %d events have aged out "
                    + "of the %d-event history buffer. Replaying full buffer; client may "
                    + "see duplicates.",
                    last_event_id,
                    bridge.session_id,
                    evictions,
                    bridge.stream_max_history,
                )
            else:
                logger.info(
                    "Replay cursor %s not in history for session %s "
                    + "(possible new turn); replaying full buffer",
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
            bridge.stream_mark_closed()
            return

    if replay_start:
        logger.debug(
            "Skipped %d already-seen events for session %s",
            replay_start,
            bridge.session_id,
        )

    # The replay tail can be empty when the cursor resolves to the last
    # buffered event. If that last event is terminal, the stream is already
    # complete: there is nothing left to replay and no live events will
    # arrive, so close immediately rather than hanging open on keepalives.
    if replay_start >= len(history) and history[-1].type in bridge.TERMINAL_EVENTS:
        bridge.stream_mark_closed()
        return

    _drain_replayed_events(bridge, replayed_ids)


# MARK: Live Streaming


def _build_keepalive_frame() -> dict[str, object]:
    """Yield an SSE comment frame as keep-alive.

    Browsers ignore comment frames entirely, so frontends do not need to
    subscribe to or filter a heartbeat event type. Matches sse-starlette's
    built-in ping shape.
    """
    utc_now: datetime = datetime.now(tz=timezone.utc)
    timestamp: str = utc_now.isoformat()
    return {"comment": f"keepalive {timestamp}"}


async def generate_sse_events(
    bridge: StreamBridge,
    *,
    last_event_id: str | None = None,
    heartbeat_interval: float | None = None,
) -> AsyncGenerator[dict[str, object], None]:
    """Yield SSE-shaped dicts for the lifetime of one client connection.

    The generator is structured so that ``GeneratorExit`` /
    ``CancelledError`` (raised by Starlette / sse-starlette when the client
    disconnects) cleanly tears down the read of ``bridge._queue`` instead
    of leaving a coroutine pending on ``queue.get()`` forever.

    Pass ``heartbeat_interval`` to override the bridge default for a
    specific stream (useful when the client lives behind a proxy with a
    short idle timeout).
    """
    interval = (
        heartbeat_interval
        if heartbeat_interval is not None
        else bridge.stream_default_heartbeat_interval
    )
    if interval <= 0:
        raise ValueError("heartbeat_interval must be > 0")

    bridge.stream_subscriber_attached()
    try:
        replayed_ids: set[str] = set()
        async for sse_frame in _replay_history(
            bridge,
            last_event_id=last_event_id,
            replayed_ids=replayed_ids,
        ):
            yield sse_frame
            if bridge.stream_is_closed:
                return

        while not bridge.stream_is_closed:
            try:
                queue_wait: Awaitable[UIEvent] = bridge.stream_queue_get()
                live_event: UIEvent = cast(
                    UIEvent,
                    await asyncio.wait_for(
                        queue_wait,
                        timeout=interval,
                    ),
                )  # pyright: ignore[reportUnnecessaryCast]
                if live_event.id in replayed_ids:
                    continue
                yield live_event.to_sse()
                if live_event.type in bridge.TERMINAL_EVENTS:
                    bridge.stream_mark_closed()
                    break
            except TimeoutError:
                yield _build_keepalive_frame()

    except (asyncio.CancelledError, GeneratorExit):
        logger.debug("SSE stream cancelled for session %s", bridge.session_id)
        # Re-raise GeneratorExit per PEP 525 so the runtime knows the
        # generator finalized cleanly. CancelledError likewise should not
        # be swallowed silently in newer Python.
        raise
    finally:
        bridge.stream_subscriber_detached()


# MARK: Lifecycle


def reopen_bridge(bridge: StreamBridge) -> None:
    bridge.stream_reset_state()
