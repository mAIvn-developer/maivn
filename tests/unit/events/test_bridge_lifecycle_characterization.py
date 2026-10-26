"""Characterization of EventBridge construction, emit helpers, queueing and SSE streaming.

Covers the bridge surface frontends rely on: constructor validation, the typed
``emit_*`` helpers, interrupt/status de-duplication, live-queue backpressure,
history replay with ``Last-Event-ID`` cursors, keep-alives, subscriber
tracking, ``close``/``reopen`` and the ``BridgeRegistry``.

No async pytest plugin is configured for this package, so each test drives its
coroutine with ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, cast

import pytest

from maivn.events import BridgeRegistry, EventBridge

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Coroutine

    from maivn.events import BackpressurePolicy

SESSION = 's-life'


def _run(coroutine: Coroutine[object, object, None]) -> None:
    asyncio.run(coroutine)


def _data(bridge: EventBridge) -> list[dict[str, object]]:
    return [cast('dict[str, object]', event['data']) for event in bridge.get_history()]


def _types(bridge: EventBridge) -> list[str]:
    return [str(event['type']) for event in bridge.get_history()]


def _pick(data: dict[str, object], *keys: str) -> dict[str, object]:
    return {key: data[key] for key in keys}


def _frame_payload(frame: dict[str, object]) -> dict[str, object]:
    return cast('dict[str, object]', json.loads(cast('str', frame['data'])))


async def _drain(stream: AsyncGenerator[dict[str, object], None]) -> list[dict[str, object]]:
    try:
        return [frame async for frame in stream]
    finally:
        await stream.aclose()


# MARK: Constructor Validation


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'max_history': 0}, 'max_history must be >= 1'),
        ({'heartbeat_interval': 0.0}, 'heartbeat_interval must be > 0'),
        ({'heartbeat_interval': -1.0}, 'heartbeat_interval must be > 0'),
        ({'queue_maxsize': -1}, 'queue_maxsize must be >= 0 (0 = unbounded)'),
        (
            {'backpressure': 'drop_all'},
            "backpressure must be one of ['block', 'drop_newest', 'drop_oldest'], got 'drop_all'",
        ),
        (
            {'schema_validation': 'loud'},
            "schema_validation must be one of ['off', 'strict', 'warn'], got 'loud'",
        ),
    ],
)
def test_constructor_rejects_invalid_settings(kwargs: dict[str, object], message: str) -> None:
    """Each invalid constructor argument raises ``ValueError`` with an exact message."""
    with pytest.raises(ValueError, match=f'^{re.escape(message)}$'):
        _ = EventBridge(SESSION, **kwargs)  # pyright: ignore[reportArgumentType]


def test_constructor_accepts_boundary_values_and_exposes_settings() -> None:
    """The smallest legal values are accepted and reported back."""
    bridge = EventBridge(SESSION, max_history=1, heartbeat_interval=0.5, queue_maxsize=0)

    assert bridge.session_id == SESSION
    assert bridge.stream_max_history == 1
    assert bridge.stream_default_heartbeat_interval == 0.5  # noqa: PLR2004
    assert bridge.stream_history_evictions == 0
    assert bridge.stream_is_closed is False
    assert bridge.stream_queue_empty() is True
    assert bridge.get_history() == []
    assert frozenset({'final', 'error', 'session_end'}) == EventBridge.TERMINAL_EVENTS


def test_default_heartbeat_interval_is_fifteen_seconds() -> None:
    """Without an override the keep-alive interval is 15 seconds and history holds 500."""
    bridge = EventBridge(SESSION)

    assert bridge.stream_default_heartbeat_interval == 15.0  # noqa: PLR2004
    assert bridge.stream_max_history == 500  # noqa: PLR2004


# MARK: Typed emit helpers


def test_system_tool_helpers_build_canonical_payloads() -> None:
    """System tool start, chunk and complete keep ids, params, progress and duration."""
    bridge = EventBridge(SESSION, schema_validation='strict')

    async def _emit() -> None:
        await bridge.emit_system_tool_start(
            'web_search', 'sys-1', params={'q': 'x'}, agent_name='ag', swarm_name='sw'
        )
        await bridge.emit_system_tool_chunk('sys-1', 'partial', progress=0.5)
        await bridge.emit_system_tool_complete('sys-1', {'hits': 2}, duration_ms=12)

    _run(_emit())
    start, chunk, complete = _data(bridge)

    assert _types(bridge) == ['system_tool_start', 'system_tool_chunk', 'system_tool_complete']
    assert _pick(start, 'tool_type', 'tool_id', 'params', 'agent_name', 'swarm_name', 'tool') == {
        'tool_type': 'web_search',
        'tool_id': 'sys-1',
        'params': {'q': 'x'},
        'agent_name': 'ag',
        'swarm_name': 'sw',
        'tool': {
            'id': 'sys-1',
            'name': 'web_search',
            'type': 'system',
            'status': 'executing',
            'args': {'q': 'x'},
        },
    }
    assert _pick(chunk, 'tool_id', 'text', 'progress') == {
        'tool_id': 'sys-1',
        'text': 'partial',
        'progress': 0.5,
    }
    assert _pick(complete, 'tool_id', 'result', 'duration_ms') == {
        'tool_id': 'sys-1',
        'result': {'hits': 2},
        'duration_ms': 12,
    }


def test_assistant_status_enrichment_final_and_error_helpers() -> None:
    """Assistant, status, enrichment, final and error helpers pass every argument through."""
    bridge = EventBridge(SESSION, schema_validation='strict')

    async def _emit() -> None:
        await bridge.emit_assistant_chunk(
            'as-1',
            'Hel',
            replace_content=True,
            private_value_restorations=[{'start': 0, 'end': 1}],
        )
        await bridge.emit_status_message('as-1', 'Thinking')
        await bridge.emit_enrichment(
            phase='memory',
            message='Recalled',
            scope_id='sc-1',
            scope_name='Research',
            scope_type='agent',
            memory={'hits': 1},
            redaction={'count': 0},
            reevaluate={'why': 'x'},
        )
        await bridge.emit_final('done', result={'a': 1})
        await bridge.emit_error('boom', details={'code': 'E1'})

    _run(_emit())
    chunk, status, enrichment, final, error = _data(bridge)

    assert _pick(
        chunk, 'assistant_id', 'text', 'replace_content', 'private_value_restorations'
    ) == {
        'assistant_id': 'as-1',
        'text': 'Hel',
        'replace_content': True,
        'private_value_restorations': [{'start': 0, 'end': 1}],
    }
    assert _pick(status, 'assistant_id', 'message') == {
        'assistant_id': 'as-1',
        'message': 'Thinking',
    }
    assert _pick(
        enrichment, 'phase', 'message', 'scope_id', 'scope_name', 'scope_type', 'enrichment'
    ) == {
        'phase': 'memory',
        'message': 'Recalled',
        'scope_id': 'sc-1',
        'scope_name': 'Research',
        'scope_type': 'agent',
        'enrichment': {
            'phase': 'memory',
            'message': 'Recalled',
            'memory': {'hits': 1},
            'redaction': {'count': 0},
            'reevaluate': {'why': 'x'},
        },
    }
    assert _pick(final, 'response', 'result', 'responses') == {
        'response': 'done',
        'result': {'a': 1},
        'responses': ['done'],
    }
    assert _pick(error, 'error', 'details', 'error_info') == {
        'error': 'boom',
        'details': {'code': 'E1'},
        'error_info': {'message': 'boom', 'details': {'code': 'E1'}},
    }


def test_hook_assignment_interrupt_and_tool_helpers() -> None:
    """Hook, assignment, interrupt and tool helpers pass every argument through."""
    bridge = EventBridge(SESSION, schema_validation='strict')

    async def _emit() -> None:
        await bridge.emit_hook_fired(
            name='audit',
            stage='before',
            status='failed',
            target_type='tool',
            target_id='t1',
            target_name='lookup',
            source='tool',
            error='hook failed',
            elapsed_ms=3,
        )
        await bridge.emit_agent_assignment(
            'Researcher',
            'in_progress',
            assignment_id='as-9',
            swarm_name='Crew',
            task='dig',
            result={'x': 1},
            use_as_final_output=True,
        )
        await bridge.emit_interrupt_required(
            'i-1',
            'email',
            'Your email?',
            arg_name='addr',
            tool_name='signup',
            checkpoint_id='cp',
            assignment_id='as-9',
            interrupt_number=2,
            total_interrupts=3,
            input_type='choice',
            choices=['a', 'b'],
        )
        await bridge.emit_tool_event(
            'lookup',
            'tool-1',
            'executing',
            args={'q': 1},
            agent_name='ag',
            swarm_name='sw',
            tool_type='func',
            duration_ms=5,
            private_data_keys=['ssn'],
        )

    _run(_emit())
    hook, assignment, interrupt, tool = _data(bridge)

    assert hook['hook'] == {
        'name': 'audit',
        'stage': 'before',
        'status': 'failed',
        'target_type': 'tool',
        'target_id': 't1',
        'target_name': 'lookup',
        'source': 'tool',
        'error': 'hook failed',
        'elapsed_ms': 3,
    }
    assert assignment['assignment'] == {
        'id': 'as-9',
        'agent_name': 'Researcher',
        'status': 'in_progress',
        'task': 'dig',
        'swarm_name': 'Crew',
        'result': {'x': 1},
        'error': None,
        'use_as_final_output': True,
    }
    assert interrupt['interrupt'] == {
        'id': 'i-1',
        'checkpoint_id': 'cp',
        'assignment_id': 'as-9',
        'data_key': 'email',
        'arg_name': 'addr',
        'prompt': 'Your email?',
        'tool_name': 'signup',
        'input_type': 'choice',
        'choices': ['a', 'b'],
        'number': 2,
        'total': 3,
    }
    assert isinstance(interrupt['timestamp'], str)
    assert datetime.fromisoformat(interrupt['timestamp']).tzinfo is not None
    assert tool['tool'] == {
        'id': 'tool-1',
        'name': 'lookup',
        'type': 'func',
        'status': 'executing',
        'args': {'q': 1},
        'result': None,
        'error': None,
        'duration_ms': 5,
        'private_data_keys': ['ssn'],
    }
    assert _pick(tool, 'agent_name', 'swarm_name') == {'agent_name': 'ag', 'swarm_name': 'sw'}


def test_interrupt_arg_name_defaults_to_data_key() -> None:
    """Without ``arg_name`` the interrupt reports its ``data_key`` as the argument."""
    bridge = EventBridge(SESSION)
    _run(bridge.emit_interrupt_required('i-1', 'email', 'Your email?'))

    interrupt = cast('dict[str, object]', _data(bridge)[0]['interrupt'])
    assert _pick(interrupt, 'arg_name', 'number', 'total', 'input_type', 'choices') == {
        'arg_name': 'email',
        'number': 1,
        'total': 1,
        'input_type': 'text',
        'choices': [],
    }


# MARK: Interrupt And Status Dedupe


def test_interrupts_dedupe_on_prompt_and_field_ignoring_case_and_space() -> None:
    """Logical duplicates are dropped; ``arg_name`` wins over ``data_key`` in the key."""
    bridge = EventBridge(SESSION)

    async def _emit() -> None:
        await bridge.emit_interrupt_required('i1', 'email', 'Your email?', arg_name='addr')
        await bridge.emit_interrupt_required('i2', 'other', '  YOUR EMAIL?  ', arg_name=' ADDR ')
        await bridge.emit_interrupt_required('i3', 'addr', 'Your email?')
        await bridge.emit_interrupt_required('i4', 'email', 'Your phone?', arg_name='addr')
        await bridge.emit(
            'interrupt_required',
            {'interrupt_id': 'i5', 'data_key': 'zip', 'prompt': 'Zip?'},
        )
        await bridge.emit(
            'interrupt_required',
            {'interrupt_id': 'i6', 'data_key': 'ZIP', 'prompt': ' zip? '},
        )

    _run(_emit())

    assert [data['interrupt_id'] for data in _data(bridge)] == ['i1', 'i4', 'i5']


def test_interrupt_dedupe_can_be_disabled() -> None:
    """``dedupe_interrupts=False`` delivers every interrupt."""
    bridge = EventBridge(SESSION, dedupe_interrupts=False)

    async def _emit() -> None:
        await bridge.emit_interrupt_required('i1', 'email', 'Your email?')
        await bridge.emit_interrupt_required('i2', 'email', 'Your email?')
        await bridge.emit(
            'interrupt_required',
            {'interrupt_id': 'i3', 'data_key': 'email', 'prompt': 'Your email?'},
        )

    _run(_emit())

    assert [data['interrupt_id'] for data in _data(bridge)] == ['i1', 'i2', 'i3']


@pytest.mark.parametrize(('reset', 'expected'), [(True, ['i1', 'i2']), (False, ['i1'])])
def test_session_start_resets_interrupt_dedupe_when_enabled(
    *,
    reset: bool,
    expected: list[str],
) -> None:
    """A ``session_start`` packet starts a new dedupe window unless disabled."""
    bridge = EventBridge(SESSION, reset_on_session_start=reset, schema_validation='off')

    async def _emit() -> None:
        await bridge.emit_interrupt_required('i1', 'email', 'Your email?')
        await bridge.emit('session_start', {'session_id': SESSION})
        await bridge.emit_interrupt_required('i2', 'email', 'Your email?')

    _run(_emit())

    ids = [data['interrupt_id'] for data in _data(bridge) if 'interrupt_id' in data]
    assert ids == expected


def test_status_messages_dedupe_only_consecutive_repeats_when_enabled() -> None:
    """Consecutive identical status messages collapse; a change in between re-arms."""
    bridge = EventBridge(SESSION, dedupe_status_messages=True)

    async def _emit() -> None:
        await bridge.emit_status_message('A1', 'Working')
        await bridge.emit_status_message(' a1 ', ' Working ')
        await bridge.emit(
            'status_message', {'assistant': {'id': 'a1'}, 'status': {'message': 'Working'}}
        )
        await bridge.emit_status_message('a1', 'Done')
        await bridge.emit_status_message('a1', 'Working')
        await bridge.emit_status_message('a2', 'Working')
        await bridge.emit_status_message('a2', '   ')
        await bridge.emit_status_message('a2', '   ')

    _run(_emit())

    assert [(data['assistant_id'], data['message']) for data in _data(bridge)] == [
        ('A1', 'Working'),
        ('a1', 'Done'),
        ('a1', 'Working'),
        ('a2', 'Working'),
        ('a2', '   '),
        ('a2', '   '),
    ]


def test_status_messages_are_not_deduped_by_default() -> None:
    """Without opting in, repeated status messages are all delivered."""
    bridge = EventBridge(SESSION)

    async def _emit() -> None:
        await bridge.emit_status_message('a1', 'Working')
        await bridge.emit_status_message('a1', 'Working')
        await bridge.emit('status_message', {'assistant_id': 'a1', 'message': 'Working'})

    _run(_emit())

    assert len(_data(bridge)) == 3  # noqa: PLR2004


def test_session_start_resets_status_dedupe() -> None:
    """A repeated status after ``session_start`` is delivered again."""
    bridge = EventBridge(SESSION, dedupe_status_messages=True, schema_validation='off')

    async def _emit() -> None:
        await bridge.emit_status_message('a1', 'Working')
        await bridge.emit('session_start', {'session_id': SESSION})
        await bridge.emit_status_message('a1', 'Working')

    _run(_emit())

    assert _types(bridge) == ['status_message', 'session_start', 'status_message']


# MARK: Queue Backpressure


def _fill(policy: BackpressurePolicy, caplog: pytest.LogCaptureFixture) -> EventBridge:
    bridge = EventBridge(SESSION, queue_maxsize=1, backpressure=policy)

    async def _emit() -> None:
        await bridge.emit_status_message('a', 'first')
        await bridge.emit_status_message('a', 'second')

    with caplog.at_level(logging.WARNING):
        _run(_emit())
    return bridge


def test_drop_newest_keeps_the_queued_event(caplog: pytest.LogCaptureFixture) -> None:
    """A full queue rejects the new event; history still records both."""
    bridge = _fill('drop_newest', caplog)

    assert bridge.stream_queue_get_nowait().data['message'] == 'first'
    assert bridge.stream_queue_empty() is True
    assert len(bridge.get_history()) == 2  # noqa: PLR2004
    assert [record.getMessage() for record in caplog.records] == [
        f'Dropping newest event for session {SESSION} (queue full, type=status_message)'
    ]


def test_drop_oldest_replaces_the_queued_event(caplog: pytest.LogCaptureFixture) -> None:
    """A full queue evicts its oldest event to make room."""
    bridge = _fill('drop_oldest', caplog)

    assert bridge.stream_queue_get_nowait().data['message'] == 'second'
    assert bridge.stream_queue_empty() is True
    assert [record.getMessage() for record in caplog.records] == [
        f'Dropping oldest event for session {SESSION} (queue full, type=status_message)'
    ]


def test_block_waits_for_room_in_the_queue() -> None:
    """The blocking policy suspends the emitter until a consumer takes an event."""
    bridge = EventBridge(SESSION, queue_maxsize=1, backpressure='block')

    async def _emit() -> None:
        await bridge.emit_status_message('a', 'first')
        pending = asyncio.ensure_future(bridge.emit_status_message('a', 'second'))
        await asyncio.sleep(0.01)
        assert pending.done() is False
        assert bridge.stream_queue_get_nowait().data['message'] == 'first'
        await asyncio.wait_for(pending, timeout=1)
        assert bridge.stream_queue_get_nowait().data['message'] == 'second'

    _run(_emit())


def test_unbounded_queue_never_drops() -> None:
    """``queue_maxsize=0`` queues every event regardless of policy."""
    bridge = EventBridge(SESSION, backpressure='drop_newest')

    async def _emit() -> None:
        for index in range(5):
            await bridge.emit_status_message('a', f'm{index}')

    _run(_emit())

    drained = [bridge.stream_queue_get_nowait().data['message'] for _ in range(5)]
    assert drained == ['m0', 'm1', 'm2', 'm3', 'm4']


# MARK: Close And Reopen


def test_closed_bridge_drops_emits_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """After ``close()`` emits are logged and discarded."""
    bridge = EventBridge(SESSION)
    bridge.close()

    with caplog.at_level(logging.WARNING):
        _run(bridge.emit_status_message('a', 'late'))

    assert bridge.stream_is_closed is True
    assert bridge.get_history() == []
    assert bridge.stream_queue_empty() is True
    assert [record.getMessage() for record in caplog.records] == [
        f'Attempted to emit to closed bridge: {SESSION}'
    ]


def test_reopen_starts_a_clean_turn() -> None:
    """``reopen()`` clears history, queue, evictions, identity aliases and dedupe state."""
    bridge = EventBridge(SESSION, max_history=2)

    async def _first_turn() -> None:
        await bridge.emit_tool_event('lookup', 'call-a', 'executing')
        await bridge.emit_tool_event('lookup', 'call-b', 'executing')
        await bridge.emit_interrupt_required('i1', 'email', 'Your email?')

    _run(_first_turn())
    assert bridge.stream_history_evictions == 1
    bridge.close()
    bridge.reopen()

    assert bridge.stream_is_closed is False
    assert bridge.get_history() == []
    assert bridge.stream_queue_empty() is True
    assert bridge.stream_history_evictions == 0

    async def _second_turn() -> None:
        await bridge.emit_tool_event('lookup', 'call-b', 'executing')
        await bridge.emit_interrupt_required('i2', 'email', 'Your email?')

    _run(_second_turn())
    tool, interrupt = _data(bridge)
    assert tool['tool_id'] == 'call-b'
    assert interrupt['interrupt_id'] == 'i2'


# MARK: Replay and live streaming


def test_replay_yields_history_and_stops_at_a_terminal_event() -> None:
    """A reconnect after completion replays everything and the stream ends."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_final('done')
        await bridge.emit_status_message('a', 'after-final')
        frames = await _drain(bridge.generate_sse())

        assert [frame['event'] for frame in frames] == ['status_message', 'final']
        history = bridge.get_history()
        assert [frame['id'] for frame in frames] == [history[0]['id'], history[1]['id']]
        payload = _frame_payload(frames[1])
        assert _pick(payload, 'id', 'type', 'timestamp') == {
            'id': history[1]['id'],
            'type': 'final',
            'timestamp': history[1]['timestamp'],
        }
        assert bridge.stream_is_closed is True

    _run(_scenario())


def test_cursor_skips_events_the_client_already_saw() -> None:
    """``last_event_id`` replays only the events after the cursor."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_status_message('a', 'two')
        await bridge.emit_final('done')
        first_id = cast('str', bridge.get_history()[0]['id'])
        frames = await _drain(bridge.generate_sse(last_event_id=first_id))

        assert [_frame_payload(frame)['type'] for frame in frames] == ['status_message', 'final']
        assert cast('dict[str, object]', _frame_payload(frames[0])['data'])['message'] == 'two'

    _run(_scenario())


def test_cursor_on_the_terminal_event_closes_without_frames() -> None:
    """A client that already saw the terminal event gets an empty, closed stream."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_error('boom')
        last_id = cast('str', bridge.get_history()[-1]['id'])

        assert await _drain(bridge.generate_sse(last_event_id=last_id)) == []
        assert bridge.stream_is_closed is True

    _run(_scenario())


def test_unknown_cursor_replays_everything_and_logs_why(caplog: pytest.LogCaptureFixture) -> None:
    """An unknown cursor without evictions is treated as a new turn."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_final('done')
        frames = await _drain(bridge.generate_sse(last_event_id='missing'))
        assert [frame['event'] for frame in frames] == ['status_message', 'final']

    with caplog.at_level(logging.INFO, logger='maivn.events._bridge'):
        _run(_scenario())

    assert (
        f'Replay cursor missing not in history for session {SESSION} '
        '(possible new turn); replaying full buffer'
    ) in [record.getMessage() for record in caplog.records]


def test_unknown_cursor_after_evictions_warns_about_duplicates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When history has aged out, an unknown cursor warns with the eviction count."""
    bridge = EventBridge(SESSION, max_history=2)

    async def _scenario() -> None:
        for index in range(3):
            await bridge.emit_status_message('a', f'm{index}')
        await bridge.emit_final('done')
        frames = await _drain(bridge.generate_sse(last_event_id='missing'))
        assert [frame['event'] for frame in frames] == ['status_message', 'final']

    with caplog.at_level(logging.WARNING, logger='maivn.events._bridge'):
        _run(_scenario())

    assert [record.getMessage() for record in caplog.records] == [
        f'Replay cursor missing unknown for session {SESSION}; 2 events have aged out of '
        'the 2-event history buffer. Replaying full buffer; client may see duplicates.'
    ]


def test_live_events_follow_replay_without_duplicates() -> None:
    """Queued copies of replayed events are dropped; later events arrive live."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        stream = bridge.generate_sse()
        try:
            first = await anext(stream)
            await bridge.emit_status_message('a', 'two')
            second = await anext(stream)
            pending = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0)
            await bridge.emit_final('done')
            third = await asyncio.wait_for(pending, timeout=1)
            with pytest.raises(StopAsyncIteration):
                _ = await anext(stream)
        finally:
            await stream.aclose()

        messages = [_frame_payload(frame) for frame in (first, second)]
        assert [cast('dict[str, object]', item['data'])['message'] for item in messages] == [
            'one',
            'two',
        ]
        assert third['event'] == 'final'
        assert bridge.stream_is_closed is True

    _run(_scenario())


def test_idle_stream_emits_keepalive_comment_frames() -> None:
    """With nothing to send, the stream yields SSE comment frames at the interval."""
    bridge = EventBridge(SESSION, heartbeat_interval=60)

    async def _scenario() -> None:
        stream = bridge.generate_sse(heartbeat_interval=0.01)
        try:
            frame = await asyncio.wait_for(anext(stream), timeout=1)
        finally:
            await stream.aclose()

        assert list(frame) == ['comment']
        comment = cast('str', frame['comment'])
        assert comment.startswith('keepalive ')
        assert datetime.fromisoformat(comment.removeprefix('keepalive ')).tzinfo is not None

    _run(_scenario())


def test_cursor_on_last_live_event_waits_for_new_events() -> None:
    """A cursor on the newest non-terminal event replays nothing and then keeps alive."""
    bridge = EventBridge(SESSION, heartbeat_interval=0.01)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        last_id = cast('str', bridge.get_history()[-1]['id'])
        stream = bridge.generate_sse(last_event_id=last_id)
        try:
            frame = await asyncio.wait_for(anext(stream), timeout=1)
        finally:
            await stream.aclose()

        assert list(frame) == ['comment']
        assert bridge.stream_queue_empty() is True

    _run(_scenario())


def test_closing_the_bridge_mid_replay_ends_the_stream() -> None:
    """A bridge closed between replayed frames stops the stream at the next pull."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        await bridge.emit_status_message('a', 'two')
        stream = bridge.generate_sse()
        try:
            first = await anext(stream)
            bridge.close()
            with pytest.raises(StopAsyncIteration):
                _ = await anext(stream)
        finally:
            await stream.aclose()

        assert cast('dict[str, object]', _frame_payload(first)['data'])['message'] == 'one'

    _run(_scenario())


def test_second_subscriber_replays_history_without_queued_copies() -> None:
    """When the live queue was already consumed, replay still serves history."""
    bridge = EventBridge(SESSION, heartbeat_interval=0.01)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        _ = bridge.stream_queue_get_nowait()
        stream = bridge.generate_sse()
        try:
            replayed = await anext(stream)
            keepalive = await asyncio.wait_for(anext(stream), timeout=1)
        finally:
            await stream.aclose()

        assert replayed['event'] == 'status_message'
        assert list(keepalive) == ['comment']

    _run(_scenario())


def test_event_enqueued_after_replay_is_not_sent_twice() -> None:
    """A blocked emit that lands in the queue after replay is skipped as a duplicate.

    The keep-alive interval is long enough that the live read sees the late copy
    before it times out, even with coarse Windows timers.
    """
    bridge = EventBridge(SESSION, queue_maxsize=1, backpressure='block', heartbeat_interval=0.5)

    async def _scenario() -> None:
        await bridge.emit_status_message('a', 'one')
        blocked = asyncio.ensure_future(bridge.emit_status_message('a', 'two'))
        await asyncio.sleep(0)
        stream = bridge.generate_sse()
        try:
            frames = [await anext(stream), await anext(stream)]
            after = await asyncio.wait_for(anext(stream), timeout=5)
        finally:
            await stream.aclose()
        await asyncio.wait_for(blocked, timeout=1)

        messages = [cast('dict[str, object]', _frame_payload(f)['data'])['message'] for f in frames]
        assert messages == ['one', 'two']
        assert list(after) == ['comment']

    _run(_scenario())


@pytest.mark.parametrize('interval', [0.0, -1.0])
def test_stream_rejects_non_positive_heartbeat_override(interval: float) -> None:
    """A per-stream heartbeat override must be positive."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        stream = bridge.generate_sse(heartbeat_interval=interval)
        with pytest.raises(ValueError, match=r'^heartbeat_interval must be > 0$'):
            _ = await anext(stream)

    _run(_scenario())


# MARK: Subscriber Tracking


def test_wait_for_subscriber_times_out_without_a_stream() -> None:
    """No consumer within the timeout returns ``False``."""
    bridge = EventBridge(SESSION)

    async def _scenario() -> None:
        assert await bridge.wait_for_subscriber(timeout=0.01) is False

    _run(_scenario())


def test_subscriber_tracking_follows_stream_lifetime() -> None:
    """Opening a stream releases waiters; closing or cancelling the last one resets it."""
    bridge = EventBridge(SESSION, heartbeat_interval=0.01)

    async def _scenario() -> None:
        waiter = asyncio.ensure_future(bridge.wait_for_subscriber(timeout=1))
        stream = bridge.generate_sse()
        first = asyncio.ensure_future(anext(stream))
        assert await asyncio.wait_for(waiter, timeout=1) is True
        assert await bridge.wait_for_subscriber(timeout=0.01) is True
        _ = await first
        await stream.aclose()
        assert await bridge.wait_for_subscriber(timeout=0.01) is False

        other = bridge.generate_sse(heartbeat_interval=5)
        consumer = asyncio.ensure_future(anext(other))
        await asyncio.sleep(0.01)
        assert await bridge.wait_for_subscriber(timeout=0.01) is True
        _ = consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert await bridge.wait_for_subscriber(timeout=0.01) is False

    _run(_scenario())


# MARK: Bridge Registry


def test_registry_creates_replaces_and_removes_bridges() -> None:
    """The registry hands out bridges by session and closes the ones it drops."""
    registry = BridgeRegistry()

    assert registry.get('a') is None
    first = registry.create('a')
    assert isinstance(first, EventBridge)
    assert first.session_id == 'a'
    assert registry.get('a') is first

    second = registry.create('a', factory=lambda sid: EventBridge(sid, audience='frontend_safe'))
    assert registry.get('a') is second
    assert second.audience == 'frontend_safe'
    assert first.stream_is_closed is True
    assert second.stream_is_closed is False

    registry.remove('a')
    assert registry.get('a') is None
    assert second.stream_is_closed is True
    registry.remove('a')


def test_registry_clear_closes_every_bridge() -> None:
    """``clear()`` closes and forgets all registered bridges."""
    registry = BridgeRegistry()
    bridges = [registry.create(name) for name in ('a', 'b', 'c')]

    registry.clear()

    assert [registry.get(name) for name in ('a', 'b', 'c')] == [None, None, None]
    assert all(bridge.stream_is_closed for bridge in bridges)
