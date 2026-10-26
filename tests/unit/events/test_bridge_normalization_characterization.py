"""Characterization of how EventBridge canonicalizes raw packets and correlates identities.

``EventBridge.emit`` accepts raw payloads in several historical shapes (flat
fields or nested ``tool``/``assistant``/``interrupt``/... descriptors) and
rewrites the known event types into the canonical AppEvent v1 payload. Tool,
assignment and scope ids are correlated across a turn so a completion that
arrives under a new id still lands on the card its start created.

No async pytest plugin is configured for this package, so each test drives its
coroutine with ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from maivn.events import EventBridge

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


def _history(
    script: Callable[[EventBridge], Coroutine[object, object, None]],
) -> list[dict[str, object]]:
    bridge = EventBridge('s-norm', schema_validation='off')
    asyncio.run(script(bridge))
    return [cast('dict[str, object]', event['data']) for event in bridge.get_history()]


def _pick(data: dict[str, object], *keys: str) -> dict[str, object]:
    return {key: data[key] for key in keys}


def _ids(
    script: Callable[[EventBridge], Coroutine[object, object, None]], key: str
) -> list[object]:
    return [data.get(key) for data in _history(script)]


# MARK: Raw Payload Canonicalization


def test_nested_tool_descriptor_is_lifted_to_canonical_fields() -> None:
    """A ``tool`` descriptor fills the flat fields; unknown extras and participants survive."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit(
            'tool_event',
            {
                'tool': {
                    'name': 'Lookup',
                    'id': ' t-1 ',
                    'status': 'executing',
                    'type': 'FUNC',
                    'args': {'q': 1},
                    'error': ' oops ',
                },
                'extra_key': 'kept',
                'participant_key': ' pk ',
                'participant_name': 'PN',
                'participant_role': 'role',
            },
        )

    (data,) = _history(_script)

    assert _pick(
        data,
        'tool_name',
        'tool_id',
        'tool_type',
        'status',
        'args',
        'error',
        'participant',
        'extra_key',
        'event_name',
    ) == {
        'tool_name': 'Lookup',
        'tool_id': 't-1',
        'tool_type': 'FUNC',
        'status': 'executing',
        'args': {'q': 1},
        'error': 'oops',
        'participant': {'key': 'pk', 'name': 'PN', 'role': 'role'},
        'extra_key': 'kept',
        'event_name': 'tool_event',
    }


def test_flat_fields_take_precedence_over_nested_descriptors() -> None:
    """When both shapes are present, the flat field wins."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit(
            'tool_event',
            {
                'tool_name': 'flat',
                'tool_id': 'flat-id',
                'status': 'completed',
                'result': {'flat': True},
                'tool': {'name': 'nested', 'id': 'nested-id', 'result': {'nested': True}},
            },
        )

    (data,) = _history(_script)

    assert _pick(data, 'tool_name', 'tool_id', 'status', 'result') == {
        'tool_name': 'flat',
        'tool_id': 'flat-id',
        'status': 'completed',
        'result': {'flat': True},
    }


def test_empty_packets_receive_documented_defaults() -> None:
    """Missing fields fall back to stable placeholder values per event type."""

    async def _script(bridge: EventBridge) -> None:
        for event_type in (
            'tool_event',
            'system_tool_start',
            'system_tool_chunk',
            'assistant_chunk',
            'status_message',
            'interrupt_required',
            'agent_assignment',
            'enrichment',
            'final',
            'error',
        ):
            await bridge.emit(event_type, {})

    tool, start, chunk, assistant, status, interrupt, assignment, enrichment, final, error = (
        _history(_script)
    )

    assert _pick(tool, 'tool_name', 'tool_id', 'status', 'tool_type', 'args') == {
        'tool_name': 'tool',
        'tool_id': 'tool',
        'status': 'executing',
        'tool_type': 'func',
        'args': {},
    }
    assert _pick(start, 'tool_type', 'tool_id', 'params') == {
        'tool_type': 'system_tool',
        'tool_id': 'system_tool',
        'params': {},
    }
    assert _pick(chunk, 'tool_id', 'text', 'progress') == {
        'tool_id': 'system_tool',
        'text': '',
        'progress': None,
    }
    assert _pick(assistant, 'assistant_id', 'text') == {'assistant_id': 'assistant', 'text': ''}
    assert _pick(status, 'assistant_id', 'message') == {'assistant_id': 'assistant', 'message': ''}
    assert _pick(interrupt, 'interrupt_id', 'data_key', 'prompt', 'arg_name') == {
        'interrupt_id': 'interrupt',
        'data_key': 'input',
        'prompt': 'Input required.',
        'arg_name': 'input',
    }
    assert _pick(assignment, 'agent_name', 'status', 'assignment_id') == {
        'agent_name': 'unknown-agent',
        'status': 'in_progress',
        'assignment_id': 'agent:unknown-agent',
    }
    assert _pick(enrichment, 'phase', 'message') == {
        'phase': 'enrichment',
        'message': 'Enrichment update',
    }
    assert 'scope_id' not in enrichment
    assert _pick(final, 'response', 'responses', 'result') == {
        'response': '',
        'responses': [],
        'result': None,
    }
    assert _pick(error, 'error', 'details') == {'error': 'Unknown error', 'details': {}}


def test_system_tool_packets_read_nested_tool_descriptors() -> None:
    """Start, chunk and complete read ids, args, results and numeric progress."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit('system_tool_start', {'tool': {'name': 'web_search', 'args': {'q': 'x'}}})
        await bridge.emit(
            'system_tool_chunk',
            {'tool': {'id': 'system_tool:web_search'}, 'text': 5, 'progress': 'half'},
        )
        await bridge.emit('system_tool_chunk', {'tool_id': 'other', 'text': 'ok', 'progress': 2})
        await bridge.emit('system_tool_complete', {'tool': {'id': 'x', 'result': {'r': 1}}})

    start, chunk, numeric, complete = _history(_script)

    assert _pick(start, 'tool_type', 'tool_id', 'params') == {
        'tool_type': 'web_search',
        'tool_id': 'system_tool:web_search',
        'params': {'q': 'x'},
    }
    assert _pick(chunk, 'tool_id', 'text', 'progress') == {
        'tool_id': 'system_tool:web_search',
        'text': '',
        'progress': None,
    }
    assert _pick(numeric, 'tool_id', 'text', 'progress') == {
        'tool_id': 'other',
        'text': 'ok',
        'progress': 2,
    }
    assert _pick(complete, 'tool_id', 'result') == {'tool_id': 'x', 'result': {'r': 1}}


def test_assistant_status_and_interrupt_packets_read_nested_descriptors() -> None:
    """Nested assistant, status and interrupt descriptors fill the canonical fields."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit('assistant_chunk', {'assistant': {'id': 'as', 'delta': 'hi'}})
        await bridge.emit(
            'status_message', {'assistant': {'id': 'as'}, 'status': {'message': 'busy'}}
        )
        await bridge.emit(
            'interrupt_required',
            {
                'interrupt': {
                    'id': 'i',
                    'data_key': 'k',
                    'prompt': 'P',
                    'number': 2,
                    'total': 3,
                    'choices': ['a'],
                    'tool_name': 'tn',
                    'arg_name': 'an',
                    'checkpoint_id': 'cp',
                    'assignment_id': 'aid',
                    'input_type': 'choice',
                },
                'timestamp': ' 2026-01-01T00:00:00+00:00 ',
            },
        )
        await bridge.emit(
            'interrupt_required',
            {'interrupt_number': 'x', 'total_interrupts': None, 'choices': 'no', 'prompt': 'Q'},
        )

    assistant, status, interrupt, fallback = _history(_script)

    assert _pick(assistant, 'assistant_id', 'text') == {'assistant_id': 'as', 'text': 'hi'}
    assert _pick(status, 'assistant_id', 'message') == {'assistant_id': 'as', 'message': 'busy'}
    assert _pick(
        interrupt,
        'interrupt_id',
        'data_key',
        'prompt',
        'tool_name',
        'arg_name',
        'checkpoint_id',
        'assignment_id',
        'interrupt_number',
        'total_interrupts',
        'input_type',
        'choices',
        'timestamp',
    ) == {
        'interrupt_id': 'i',
        'data_key': 'k',
        'prompt': 'P',
        'tool_name': 'tn',
        'arg_name': 'an',
        'checkpoint_id': 'cp',
        'assignment_id': 'aid',
        'interrupt_number': 2,
        'total_interrupts': 3,
        'input_type': 'choice',
        'choices': ['a'],
        'timestamp': '2026-01-01T00:00:00+00:00',
    }
    assert _pick(fallback, 'interrupt_number', 'total_interrupts', 'choices', 'prompt') == {
        'interrupt_number': 1,
        'total_interrupts': 1,
        'choices': [],
        'prompt': 'Q',
    }


def test_assignment_enrichment_final_and_error_packets_read_nested_descriptors() -> None:
    """Assignment, enrichment, final output and error info descriptors are lifted."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit(
            'agent_assignment',
            {
                'assignment': {
                    'agent_name': 'Deep Researcher',
                    'swarm_name': 'Crew One',
                    'status': 'completed',
                    'task': 't',
                    'error': 'e',
                    'result': {'ok': 1},
                },
            },
        )
        await bridge.emit(
            'enrichment',
            {
                'enrichment': {
                    'phase': 'memory',
                    'message': 'm',
                    'memory': {'a': 1},
                    'redaction': {'b': 2},
                },
                'scope_name': 'Research',
                'scope_type': 'Agent',
            },
        )
        await bridge.emit(
            'final',
            {'output': {'response': 'r', 'result': {'x': 1}, 'token_usage': {'t': 1}}},
        )
        await bridge.emit('final', {'responses': ['first', 'second']})
        await bridge.emit('final', {'responses': [1]})
        await bridge.emit('error', {'error_info': {'message': 'm', 'details': {'d': 1}}})

    assignment, enrichment, final, from_responses, non_text, error = _history(_script)

    assert _pick(
        assignment, 'assignment_id', 'agent_name', 'status', 'task', 'swarm_name', 'error', 'result'
    ) == {
        'assignment_id': 'agent:crew_one:deep_researcher',
        'agent_name': 'Deep Researcher',
        'status': 'completed',
        'task': 't',
        'swarm_name': 'Crew One',
        'error': 'e',
        'result': {'ok': 1},
    }
    assert _pick(
        enrichment, 'phase', 'message', 'scope_id', 'scope_type', 'memory', 'redaction'
    ) == {
        'phase': 'memory',
        'message': 'm',
        'scope_id': 'agent:research',
        'scope_type': 'agent',
        'memory': {'a': 1},
        'redaction': {'b': 2},
    }
    assert _pick(final, 'response', 'result', 'token_usage') == {
        'response': 'r',
        'result': {'x': 1},
        'token_usage': {'t': 1},
    }
    assert from_responses['response'] == 'first'
    assert non_text['response'] == ''
    assert _pick(error, 'error', 'details') == {'error': 'm', 'details': {'d': 1}}


def test_unknown_event_types_pass_through_untouched() -> None:
    """A custom event keeps its exact type string and payload object."""
    payload: dict[str, object] = {'k': 'v'}
    bridge = EventBridge('s-norm', schema_validation='off')
    asyncio.run(bridge.emit(' Custom ', payload))

    event = bridge.stream_queue_get_nowait()
    assert event.type == ' Custom '
    assert event.data is payload


# MARK: Tool Identity


def test_completion_under_a_new_id_attaches_to_the_unique_active_call() -> None:
    """A completion id is aliased onto the single in-flight call with the same signature."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_tool_event('lookup', 'call-1', 'executing')
        await bridge.emit_tool_event('lookup', 'result-9', 'completed')
        await bridge.emit_tool_event('lookup', 'result-9', 'completed')
        await bridge.emit_tool_event('lookup', 'other', 'completed')

    assert _ids(_script, 'tool_id') == ['call-1', 'call-1', 'call-1', 'other']


def test_parallel_calls_with_different_args_stay_distinct() -> None:
    """Ambiguous completions keep their own id until only one candidate remains."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_tool_event('lookup', 'a', 'executing', args={'q': 1})
        await bridge.emit_tool_event('lookup', 'b', 'executing', args={'q': 2})
        await bridge.emit_tool_event('lookup', 'c', 'completed')
        await bridge.emit_tool_event('lookup', 'a', 'failed')
        await bridge.emit_tool_event('lookup', 'd', 'completed')

    assert _ids(_script, 'tool_id') == ['a', 'b', 'c', 'a', 'b']


def test_repeated_start_with_identical_args_collapses_onto_the_first() -> None:
    """A duplicate start reuses the first id; new args or argless starts do not match."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_tool_event('lookup', 'a', 'executing', args={'q': 1})
        await bridge.emit_tool_event('lookup', 'b', ' EXECUTING ', args={'q': 1})
        await bridge.emit_tool_event('lookup', 'c', 'executing', args={'q': 2})
        await bridge.emit_tool_event('lookup', 'd', 'executing')

    assert _ids(_script, 'tool_id') == ['a', 'a', 'c', 'd']


def test_argless_start_reuses_the_unique_active_call() -> None:
    """With no args, a second start falls back to the base signature match."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_tool_event('lookup', 'a', 'executing', args={'q': 1})
        await bridge.emit_tool_event('lookup', 'b', 'executing')
        await bridge.emit_tool_event('lookup', 'c', 'running')

    assert _ids(_script, 'tool_id') == ['a', 'a', 'a']


def test_signature_includes_tool_type_agent_and_swarm() -> None:
    """Calls made by a different agent, swarm or tool type are never merged."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_tool_event('lookup', 'a', 'executing', agent_name='Alpha')
        await bridge.emit_tool_event('lookup', 'b', 'completed', agent_name='Beta')
        await bridge.emit_tool_event('lookup', 'c', 'completed', agent_name='alpha', swarm_name='S')
        await bridge.emit_tool_event(
            'lookup', 'd', 'completed', agent_name='ALPHA', tool_type='mcp'
        )
        await bridge.emit_tool_event('lookup', 'e', 'completed', agent_name=' alpha ')

    assert _ids(_script, 'tool_id') == ['a', 'b', 'c', 'd', 'a']


def test_system_tool_chunks_and_completions_follow_aliases() -> None:
    """Once an id is aliased, chunks and completions under it use the canonical id."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_system_tool_start('web_search', 's1')
        await bridge.emit_tool_event('web_search', 's2', 'executing', tool_type='system')
        await bridge.emit_system_tool_chunk('s2', 'partial')
        await bridge.emit_system_tool_complete('s2', {'ok': True})
        await bridge.emit('system_tool_chunk', {'tool_id': 's2', 'text': 'raw'})
        await bridge.emit_system_tool_chunk('unknown', 'x')

    assert _ids(_script, 'tool_id') == ['s1', 's1', 's1', 's1', 's1', 'unknown']


# MARK: Assignment And Scope Identity


def test_assignments_correlate_by_agent_and_swarm_name() -> None:
    """The first id for an agent wins; later ids alias onto it."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_agent_assignment('Deep Researcher', 'in_progress', swarm_name='Crew One')
        await bridge.emit_agent_assignment(
            'deep researcher', 'completed', assignment_id='as-7', swarm_name=' crew one '
        )
        await bridge.emit_agent_assignment('Other', 'completed', assignment_id='as-7')
        await bridge.emit_agent_assignment('Solo', 'received', assignment_id=' as-8 ')
        await bridge.emit_agent_assignment('Solo', 'completed')
        await bridge.emit('agent_assignment', {'agent_name': 'Solo', 'assignment_id': 'as-9'})

    assert _ids(_script, 'assignment_id') == [
        'agent:crew_one:deep_researcher',
        'agent:crew_one:deep_researcher',
        'agent:crew_one:deep_researcher',
        'as-8',
        'as-8',
        'as-8',
    ]


def test_scopes_correlate_by_type_and_name() -> None:
    """Scope ids are derived from type and name, and explicit ids alias onto them."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_enrichment(phase='p', message='m')
        await bridge.emit_enrichment(
            phase='p', message='m', scope_name='Research', scope_type='Agent'
        )
        await bridge.emit_enrichment(
            phase='p', message='m', scope_id='sc-9', scope_name=' research ', scope_type='agent'
        )
        await bridge.emit_enrichment(phase='p', message='m', scope_id='sc-9')
        await bridge.emit_enrichment(phase='p', message='m', scope_id='sc-10')
        await bridge.emit_enrichment(phase='p', message='m', scope_name='Ops Team')

    assert _ids(_script, 'scope_id') == [
        None,
        'agent:research',
        'agent:research',
        'agent:research',
        'sc-10',
        'scope:ops_team',
    ]


def test_flat_interrupt_counters_and_choices_win_over_nested_ones() -> None:
    """Integer counters and a choices list on the packet override the nested interrupt."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit(
            'interrupt_required',
            {
                'interrupt_number': 4,
                'total_interrupts': 5,
                'choices': ['flat'],
                'interrupt': {'number': 1, 'total': 2, 'choices': ['nested']},
            },
        )

    (data,) = _history(_script)

    assert _pick(data, 'interrupt_number', 'total_interrupts', 'choices') == {
        'interrupt_number': 4,
        'total_interrupts': 5,
        'choices': ['flat'],
    }


def test_self_referencing_args_still_correlate_duplicate_starts() -> None:
    """Arguments that cannot be JSON-fingerprinted fall back to their ``repr``.

    Equal reprs collapse onto the first call; a different repr stays distinct.
    """

    async def _script(bridge: EventBridge) -> None:
        first: dict[str, object] = {'q': 1}
        first['self'] = first
        second: dict[str, object] = {'q': 1}
        second['self'] = second
        await bridge.emit_tool_event('lookup', 'a', 'executing', args=first)
        await bridge.emit_tool_event('lookup', 'b', 'executing', args=second)
        third: dict[str, object] = {'q': 2}
        third['self'] = third
        await bridge.emit_tool_event('lookup', 'c', 'executing', args=third)

    assert _ids(_script, 'tool_id') == ['a', 'a', 'c']


def test_named_tool_without_id_gets_a_slugged_fallback_id() -> None:
    """A raw tool packet without an id is keyed by its slugged name."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit('tool_event', {'tool_name': 'My Lookup'})
        await bridge.emit('system_tool_start', {'tool_type': 'Web Search'})

    assert _ids(_script, 'tool_id') == ['tool:my_lookup', 'system_tool:web_search']


def test_blank_agent_name_shares_the_unknown_agent_card() -> None:
    """A blank agent name and ``unknown-agent`` resolve to the same assignment id."""

    async def _script(bridge: EventBridge) -> None:
        await bridge.emit_agent_assignment('  ', 'in_progress')
        await bridge.emit_agent_assignment('unknown-agent', 'completed')

    assert _ids(_script, 'assignment_id') == ['agent', 'agent']
