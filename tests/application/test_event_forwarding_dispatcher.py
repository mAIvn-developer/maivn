"""Dispatcher-coverage tests for ``events/_forwarding/reporter``.

These guard against the historical risk of the if/elif (now dict) dispatcher
falling out of sync with the set of normalized event names the SDK emits.
If you add a new normalized event:

  1. Add the forwarder in the appropriate submodule under
     ``events/_forwarding/reporter/`` (``session.py``, ``tools.py``, …).
  2. Wire it into ``dispatcher._DISPATCHERS``.
  3. Update the expected set below.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from maivn import NormalizedEventForwardingState
from maivn.events._forwarding.reporter import forward_to_reporter, known_event_names

# Snapshot of every normalized event name the forwarding dispatcher handles.
# Adding to this set requires wiring a forwarder. Removing requires removing
# the corresponding submodule code, not just deleting the entry.
EXPECTED_EVENT_NAMES = frozenset(
    {
        "agent_assignment",
        "assistant_chunk",
        "enrichment",
        "error",
        "final",
        "hook_fired",
        "session_start",
        "status_message",
        "system_tool_chunk",
        "system_tool_complete",
        "system_tool_error",
        "system_tool_start",
        "tool_event",
    }
)


def test_dispatcher_covers_expected_event_names() -> None:
    """If this fails, the dispatcher dict drifted from the documented contract.

    Decide whether the change is intentional (update ``EXPECTED_EVENT_NAMES``)
    or accidental (re-add the dispatcher entry / forwarder).
    """
    assert known_event_names() == EXPECTED_EVENT_NAMES


def test_dispatcher_ignores_unknown_event_name() -> None:
    """Unknown ``event_name`` values must silently no-op, not crash."""
    event = MagicMock()
    event.event_name = "this_event_does_not_exist"
    reporter = MagicMock()
    state = NormalizedEventForwardingState()

    forward_to_reporter(event, payload={}, reporter=reporter, state=state)

    # Reporter should not have been touched at all.
    assert reporter.mock_calls == []


def test_dispatcher_ignores_empty_event_name() -> None:
    """An empty / missing ``event_name`` must also silently no-op."""
    event = MagicMock()
    event.event_name = ""
    reporter = MagicMock()
    state = NormalizedEventForwardingState()

    forward_to_reporter(event, payload={}, reporter=reporter, state=state)

    assert reporter.mock_calls == []
