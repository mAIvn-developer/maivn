"""Public helpers for replaying normalized AppEvents into reporters and bridges."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ._bridge import EventBridge
from ._forwarding import NormalizedEventForwardingState, forward_to_bridge, forward_to_reporter
from ._models import AppEvent

# MARK: Public API


async def forward_normalized_event(
    event: AppEvent,
    *,
    reporter: Any | None = None,
    bridge: EventBridge | None = None,
    state: NormalizedEventForwardingState | None = None,
) -> NormalizedEventForwardingState:
    """Forward a normalized AppEvent into a reporter and/or UI bridge."""
    active_state = state or NormalizedEventForwardingState()
    payload = event.model_dump(mode="python")

    if reporter is not None:
        forward_to_reporter(event, payload=payload, reporter=reporter, state=active_state)
    if bridge is not None:
        await forward_to_bridge(event, payload=payload, bridge=bridge, state=active_state)

    return active_state


async def forward_normalized_stream(
    events: Iterable[AppEvent],
    *,
    reporter: Any | None = None,
    bridge: EventBridge | None = None,
    state: NormalizedEventForwardingState | None = None,
) -> NormalizedEventForwardingState:
    """Forward a normalized AppEvent stream with shared streaming state."""
    active_state = state or NormalizedEventForwardingState()
    for event in events:
        await forward_normalized_event(
            event,
            reporter=reporter,
            bridge=bridge,
            state=active_state,
        )
    return active_state


__all__ = [
    "NormalizedEventForwardingState",
    "forward_normalized_event",
    "forward_normalized_stream",
]
