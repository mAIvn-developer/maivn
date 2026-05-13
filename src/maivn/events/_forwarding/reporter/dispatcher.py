"""Dispatcher that routes normalized AppEvents to reporter methods."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..._models import AppEvent
from ..payload import normalized_text
from ..state import NormalizedEventForwardingState
from .assignment import forward_agent_assignment, forward_enrichment
from .hooks import forward_hook_fired
from .session import forward_assistant_chunk, forward_session_start, forward_status_message
from .terminal import forward_error, forward_final
from .tools import (
    forward_system_tool_chunk,
    forward_system_tool_complete,
    forward_system_tool_error,
    forward_system_tool_start,
    forward_tool_event,
)

# MARK: Dispatcher type


Forwarder = Callable[..., None]
"""Signature: ``(event, *, payload, reporter, state) -> None``."""


# MARK: Dispatch Table

# Maps the normalized ``event_name`` to the forwarder responsible for it.
# Adding a new event = add an entry here + write the forwarder. The dispatcher
# itself stays a single dict lookup, and the test
# ``test_forwarding_dispatcher_covers_known_events`` asserts this map matches
# the set of names the rest of the SDK emits, so a typo or missed entry trips
# CI rather than silently dropping events.
_DISPATCHERS: dict[str, Forwarder] = {
    "session_start": forward_session_start,
    "assistant_chunk": forward_assistant_chunk,
    "status_message": forward_status_message,
    "agent_assignment": forward_agent_assignment,
    "enrichment": forward_enrichment,
    "tool_event": forward_tool_event,
    "system_tool_start": forward_system_tool_start,
    "system_tool_chunk": forward_system_tool_chunk,
    "system_tool_complete": forward_system_tool_complete,
    "system_tool_error": forward_system_tool_error,
    "hook_fired": forward_hook_fired,
    "final": forward_final,
    "error": forward_error,
}


# MARK: Public API


def forward_to_reporter(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    """Route a normalized AppEvent to the matching reporter forwarder."""
    event_name = normalized_text(event.event_name) or ""
    forwarder = _DISPATCHERS.get(event_name)
    if forwarder is None:
        return
    forwarder(event, payload=payload, reporter=reporter, state=state)


def known_event_names() -> frozenset[str]:
    """Return the set of event names the dispatcher currently handles.

    Exported for the dispatcher-coverage test.
    """
    return frozenset(_DISPATCHERS)


__all__ = [
    "forward_to_reporter",
    "known_event_names",
]
