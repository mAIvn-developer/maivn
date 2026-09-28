"""What the bounded event history is allowed to forget.

BENCH-2026-08-16 (claude): found by the Studio demo benchmark. `multi_result_demo`
displayed no tool activity at all while its own `turn_complete` named six tools, and
`swarm_invoke_demo` did the same with three. Both sessions returned exactly 500 events -
the cap - and both were missing `session_start` and `enrichment` as well, because the
buffer evicted oldest-first and 498 assistant text chunks had pushed every structural
event out of the front.

No async plugin is configured for this package, so each test drives its own coroutine via
``asyncio.run()``, matching `test_bridge_on_packet.py`.
"""

from __future__ import annotations

import asyncio
from typing import cast

from maivn.events import EventBridge

_FLOOD_HISTORY_LIMIT = 20
_TOOL_EVENT_COUNT = 3
_STRUCTURAL_HISTORY_LIMIT = 10
_COUNTED_HISTORY_LIMIT = 5
_COUNTED_EVENTS = 25


def _emit(bridge: EventBridge, event_type: str, index: int) -> None:
    asyncio.run(bridge.emit(event_type, {'i': index}))


def test_a_flood_of_chunks_does_not_evict_the_tool_calls() -> None:
    """The exact shape of the failure: structural events first, then a chunk flood."""
    bridge = EventBridge('s-flood', max_history=_FLOOD_HISTORY_LIMIT, schema_validation='off')

    _emit(bridge, 'session_start', 0)
    for index in range(_TOOL_EVENT_COUNT):
        _emit(bridge, 'tool_event', index)
    for index in range(200):
        _emit(bridge, 'assistant_chunk', index)

    history = bridge.get_history()
    kinds = [str(event['type']) for event in history]

    assert len(history) <= _FLOOD_HISTORY_LIMIT
    assert kinds.count('tool_event') == _TOOL_EVENT_COUNT, (
        'a tool call must never be evicted by chunk text'
    )
    assert 'session_start' in kinds
    assert 'assistant_chunk' in kinds, 'the cap must still be spent on something'


def test_the_cap_still_holds_when_nothing_is_evictable() -> None:
    """Structural events are still dropped once there is no chunk left to drop.

    Otherwise the "keep the important ones" rule would quietly become an unbounded buffer.
    """
    bridge = EventBridge(
        's-structural', max_history=_STRUCTURAL_HISTORY_LIMIT, schema_validation='off'
    )

    for index in range(50):
        _emit(bridge, 'tool_event', index)

    history = bridge.get_history()

    assert len(history) == _STRUCTURAL_HISTORY_LIMIT
    assert all(event['type'] == 'tool_event' for event in history)
    assert [cast('dict[str, object]', event['data'])['i'] for event in history] == list(
        range(40, 50)
    ), 'oldest-first'


def test_eviction_is_still_counted() -> None:
    """Truncation stays observable - it is what makes a short trace explicable."""
    bridge = EventBridge('s-counted', max_history=_COUNTED_HISTORY_LIMIT, schema_validation='off')

    for index in range(_COUNTED_EVENTS):
        _emit(bridge, 'assistant_chunk', index)

    assert bridge.stream_history_evictions == _COUNTED_EVENTS - _COUNTED_HISTORY_LIMIT
    assert len(bridge.get_history()) == _COUNTED_HISTORY_LIMIT


def test_chunks_are_evicted_oldest_first() -> None:
    """Among evictable events the oldest still goes first, so recent context survives."""
    bridge = EventBridge('s-order', max_history=3, schema_validation='off')

    for index in range(6):
        _emit(bridge, 'assistant_chunk', index)

    assert [cast('dict[str, object]', event['data'])['i'] for event in bridge.get_history()] == [
        3,
        4,
        5,
    ]
