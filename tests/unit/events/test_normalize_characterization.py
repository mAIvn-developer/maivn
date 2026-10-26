"""Characterization of ``normalize_stream_event`` / ``normalize_stream``.

Raw SSE events from the server are turned into zero or more AppEvents. These
tests pin the output of each handler: tool calls and their identity/metadata
resolution, model-tool completions flushed by ``final``, assistant streaming
deltas and replacement, swarm assignment projection, system tool lifecycles,
enrichment, interrupts, errors, hooks, session starts and v1 passthrough.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from maivn.events import (
    AppEvent,
    NormalizedStreamState,
    RawSSEEvent,
    normalize_stream,
    normalize_stream_event,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from maivn._internal.models import JsonObject


def _normalize(
    name: str,
    payload: dict[str, JsonValue],
    *,
    state: NormalizedStreamState | None = None,
    **options: object,
) -> list[dict[str, object]]:
    events = normalize_stream_event(
        RawSSEEvent(name=name, payload=payload),
        state=state,
        **options,  # pyright: ignore[reportArgumentType]
    )
    return [event.model_dump(exclude_none=True) for event in events]


def _tool(event: dict[str, object]) -> object:
    return event['tool']


# MARK: Tool Calls


def test_tool_calls_resolve_ids_names_types_and_scope() -> None:
    """Each tool call gets an id, display name, type and agent/swarm attribution."""
    state = NormalizedStreamState()
    metadata: dict[str, JsonObject] = {
        'agent-uuid': {
            'tool_name': 'Researcher',
            'tool_type': 'Agent',
            'target_agent_id': 'ag-9',
            'swarm_name': 'Crew',
        },
    }

    events = _normalize(
        'tool_event',
        {
            'id': 'ev',
            'value': {
                'tool_calls': [
                    {'name': 'lookup', 'args': {'q': 1}},
                    {'tool_id': 'explicit', 'name': 'calc', 'type': 'MCP'},
                    {'id': 'agent-uuid', 'name': 'agent-uuid', 'args': {'task': 'x'}},
                    'junk',
                ],
            },
        },
        state=state,
        default_agent_name='Default',
        default_swarm_name='Swarm',
        default_participant_key='pk',
        tool_metadata_map=metadata,
    )

    assert [_tool(event) for event in events] == [
        {
            'id': 'ev:0:lookup',
            'name': 'lookup',
            'type': 'func',
            'status': 'executing',
            'args': {'q': 1},
            'private_data_keys': [],
        },
        {
            'id': 'explicit',
            'name': 'calc',
            'type': 'mcp',
            'status': 'executing',
            'args': {},
            'private_data_keys': [],
        },
        {
            'id': 'agent-uuid',
            'name': 'Researcher',
            'type': 'agent',
            'status': 'executing',
            'args': {'task': 'x', 'agent_id': 'ag-9'},
            'private_data_keys': [],
        },
    ]
    assert [(event['agent_name'], event['swarm_name']) for event in events] == [
        ('Default', 'Swarm'),
        ('Default', 'Swarm'),
        ('Researcher', 'Crew'),
    ]
    assert events[0]['participant'] == {'key': 'pk'}
    assert state.reported_tool_ids == {'ev:0:lookup', 'explicit', 'agent-uuid'}


def test_repeated_tool_ids_are_reported_once() -> None:
    """A tool call id already reported in this stream is skipped."""
    state = NormalizedStreamState()
    payload: dict[str, JsonValue] = {'value': {'tool_calls': [{'tool_id': 'same', 'name': 'x'}]}}

    assert len(_normalize('tool_event', payload, state=state)) == 1
    assert _normalize('tool_event', payload, state=state) == []


def test_single_tool_call_and_name_fallbacks() -> None:
    """``tool_call`` is accepted alone; names come from maps, explicit names or the id."""
    by_name = _normalize('tool_event', {'value': {'tool_call': {'name': 'x:y'}}})
    mapped = _normalize(
        'tool_event',
        {'value': {'tool_calls': [{'id': 'ns:fn'}]}},
        tool_name_map={'ns:fn': 'Mapped'},
    )
    from_id = _normalize('tool_event', {'value': {'tool_calls': [{'id': 'ns:fn'}]}})
    plain_id = _normalize('tool_event', {'value': {'tool_calls': [{'id': 'fn', 'tool_type': 'X'}]}})

    assert [(event['tool_id'], event['tool_name']) for event in by_name] == [('x:y', 'x:y')]
    assert [(event['tool_id'], event['tool_name']) for event in mapped] == [('ns:fn', 'Mapped')]
    assert [(event['tool_id'], event['tool_name']) for event in from_id] == [('ns:fn', 'fn')]
    assert [(event['tool_name'], event['tool_type']) for event in plain_id] == [('fn', 'x')]


def test_agent_metadata_keeps_explicit_agent_id_and_default_attribution() -> None:
    """An explicit ``agent_id`` argument wins; metadata without names falls back."""
    events = _normalize(
        'tool_event',
        {
            'value': {
                'tool_calls': [
                    {'id': 'a1', 'name': 'Helper', 'type': 'agent', 'args': {'agent_id': 'own'}},
                    {'id': 'a2', 'name': 'Bare', 'type': 'agent'},
                ],
            },
        },
        default_agent_name='Default',
        tool_metadata_map={'a1': {'target_agent_id': 'meta'}, 'a2': {'tool_type': ' '}},
    )

    assert [(event['tool_name'], event['agent_name'], event['args']) for event in events] == [
        ('Helper', 'Helper', {'agent_id': 'own'}),
        ('Bare', 'Bare', {}),
    ]


def test_tool_events_without_calls_produce_nothing() -> None:
    """Missing, empty or unidentifiable calls are dropped."""
    assert _normalize('tool_event', {}) == []
    assert _normalize('tool_event', {'value': {'tool_calls': []}}) == []
    assert _normalize('tool_event', {'value': {'tool_call': {}}}) == []
    assert _normalize('tool_event', {'value': {'tool_call': 'text'}}) == []


# MARK: Model Tools And Final


def test_model_tool_completions_are_flushed_by_final() -> None:
    """Pending model tools complete on ``final``; only the last carries the structured result."""
    state = NormalizedStreamState()

    started = _normalize(
        'model_tool_complete',
        {'tool_name': 'draft', 'event_id': 'm1'},
        state=state,
        default_agent_name='A',
    )
    second = _normalize(
        'model_tool_complete',
        {'tool_name': 'structured', 'event_id': 'm2', 'result': {'a': 1}},
        state=state,
    )
    final = _normalize(
        'final',
        {'responses': ['  ', 'last answer', ''], 'token_usage': {'t': 1}},
        state=state,
    )

    assert [(event['tool_id'], event['status'], event['tool_type']) for event in started] == [
        ('m1', 'executing', 'model'),
    ]
    assert started[0]['agent_name'] == 'A'
    assert second[0]['tool_name'] == 'structured'
    assert [
        (event['event_name'], event.get('tool_id'), event.get('result')) for event in final
    ] == [
        ('tool_event', 'm1', None),
        ('tool_event', 'm2', {'a': 1}),
        ('final', None, {'a': 1}),
    ]
    assert final[2]['output'] == {
        'response': 'last answer',
        'result': {'a': 1},
        'token_usage': {'t': 1},
    }
    assert state.pending_model_tools == []


def test_model_tool_without_event_id_gets_a_uuid() -> None:
    """A model tool completion without an id is assigned a random UUID."""
    (event,) = _normalize('model_tool_complete', {})

    assert event['tool_name'] == 'model_tool'
    assert uuid.UUID(str(event['tool_id'])).version == 4  # noqa: PLR2004


def test_final_prefers_latest_response_and_explicit_result() -> None:
    """``response`` is trimmed; an explicit result also completes the pending model tool."""
    state = NormalizedStreamState()
    _ = _normalize('model_tool_complete', {'event_id': 'm', 'result': {'model': 1}}, state=state)

    final = _normalize('final', {'response': '  hi  ', 'result': {'k': 'v'}}, state=state)

    assert final[-1]['response'] == 'hi'
    assert final[-1]['result'] == {'k': 'v'}
    assert final[0]['result'] == {'k': 'v'}


def test_final_rebases_private_restorations_onto_the_trimmed_response() -> None:
    """Restoration spans move by the stripped leading whitespace onto every response path."""
    (final,) = _normalize(
        'final',
        {
            'response': '  Hi Jane',
            'private_value_restorations': [
                {'path': '/response', 'start': 5, 'end': 9, 'key': 'name'},
            ],
        },
    )

    assert final['private_value_restorations'] == [
        {'path': '/response', 'start': 3, 'end': 7, 'key': 'name'},
        {'path': '/responses/0', 'start': 3, 'end': 7, 'key': 'name'},
        {'path': '/output/response', 'start': 3, 'end': 7, 'key': 'name'},
    ]


# MARK: Assistant Streaming


def test_cumulative_updates_become_deltas_and_divergence_replaces() -> None:
    """Snapshots emit only new text; a diverging snapshot replaces the bubble."""
    state = NormalizedStreamState()
    outputs = [
        _normalize('update', {'streaming_content': content, 'assistant_id': 'a'}, state=state)
        for content in ('Hel', 'Hello', 'Hello', 'He', 'Bye now')
    ]

    assert [[(event['text'], event['assistant']) for event in out] for out in outputs] == [
        [('Hel', {'id': 'a', 'delta': 'Hel', 'replace_content': False})],
        [('lo', {'id': 'a', 'delta': 'lo', 'replace_content': False})],
        [],
        [],
        [('Bye now', {'id': 'a', 'delta': 'Bye now', 'replace_content': True})],
    ]
    assert state.streaming_text_by_id == {'a': 'Bye now'}


def test_reevaluate_forces_the_next_chunk_to_replace() -> None:
    """``reevaluate_accrued`` clears cached text and the next chunk replaces in full."""
    state = NormalizedStreamState()
    _ = _normalize('update', {'streaming_content': 'Old answer'}, state=state)

    enrichment = _normalize(
        'enrichment',
        {'phase': 'reevaluate_accrued', 'reevaluate': {'why': 'x'}, 'redaction': {'r': 1}},
        state=state,
    )
    replaced = _normalize('update', {'streaming_content': 'New', 'assistant_id': 'b'}, state=state)
    appended = _normalize(
        'update', {'streaming_content': 'New text', 'assistant_id': 'b'}, state=state
    )

    assert enrichment[0]['enrichment'] == {
        'phase': 'reevaluate_accrued',
        'message': 'reevaluate_accrued',
        'redaction': {'r': 1},
        'reevaluate': {'why': 'x'},
    }
    assert [(event['text'], event.get('replace_content')) for event in replaced] == [('New', True)]
    assert [(event['text'], event.get('replace_content')) for event in appended] == [
        (' text', None)
    ]
    assert state.next_assistant_chunk_replaces is False


def test_progress_status_and_status_chunk_events() -> None:
    """Progress text, status messages and status chunks normalize with defaults."""
    progress = _normalize('progress_update', {'text': 'step 1', 'assistant_id': 'a'})
    status = _normalize('status_message', {'message': ' hi ', 'assistant_id': 'a'})
    chunk = _normalize('status_message_chunk', {'text': 'par', 'assistant_id': 'a'})
    final_chunk = _normalize('status_message_chunk', {'is_final': True, 'status_id': 'st'})
    final_flag = _normalize('status_message_chunk', {'final': True})

    assert [(event['event_name'], event['text']) for event in progress] == [
        ('assistant_chunk', 'step 1')
    ]
    assert [(event['assistant_id'], event['message']) for event in status] == [('a', 'hi')]
    assert [event['status'] for event in chunk] == [
        {'id': 'status:a', 'delta': 'par', 'final': False}
    ]
    assert [event['status'] for event in final_chunk] == [{'id': 'st', 'delta': '', 'final': True}]
    assert [event['status_id'] for event in final_flag] == ['status:assistant']


def test_empty_assistant_events_produce_nothing() -> None:
    """Empty progress, blank status and empty status chunks are dropped."""
    assert _normalize('progress_update', {'text': ''}) == []
    assert _normalize('progress_update', {}) == []
    assert _normalize('status_message', {'message': ' '}) == []
    assert _normalize('status_message_chunk', {}) == []
    assert _normalize('update', {'other': 1}) == []


def test_swarm_member_updates_project_assignments() -> None:
    """``swarm_agent`` actions become assignment events with mapped status and name."""
    mapped = _normalize(
        'update',
        {
            'action_type': 'swarm_agent',
            'action_id': 'act-1',
            'status': 'done',
            'result': {'r': 1},
            'task': 'dig',
            'use_as_final_output': True,
        },
        assignment_name_map={'act-1': 'Mapped Agent'},
        default_swarm_name='Crew',
    )
    named = _normalize(
        'progress_update',
        {
            'action_type': 'swarm_agent',
            'action_name': 'Named',
            'action_id': 'act-2',
            'status': 'queued',
            'result': {'r': 1},
        },
    )
    by_id = _normalize(
        'update', {'action_type': 'swarm_agent', 'action_id': 'act-3', 'status': 'error'}
    )
    unknown = _normalize('update', {'action_type': 'swarm_agent', 'status': 'weird'})

    assert mapped[0]['assignment'] == {
        'id': 'act-1',
        'agent_name': 'Mapped Agent',
        'status': 'completed',
        'task': 'dig',
        'swarm_name': 'Crew',
        'result': {'r': 1},
        'use_as_final_output': True,
    }
    assert named[0]['assignment'] == {
        'id': 'act-2',
        'agent_name': 'Named',
        'status': 'received',
        'use_as_final_output': False,
    }
    assert [(event['agent_name'], event['status']) for event in by_id] == [('act-3', 'failed')]
    assert [(event['agent_name'], event['status']) for event in unknown] == [
        ('unknown-agent', 'in_progress')
    ]


# MARK: System Tools


def test_system_tool_lifecycle_uses_metadata_and_started_ids() -> None:
    """Start classifies via metadata; chunks find the single started tool; completion pops it."""
    state = NormalizedStreamState()

    start = _normalize(
        'system_tool_start',
        {
            'tool_name': 'web_search',
            'tool_id': 'call-1',
            'params': {'q': 'x'},
            'agent_name': 'ag',
            'private_data_keys': ['k', '', 3],
        },
        state=state,
        tool_metadata_map={'web_search': {'tool_type': 'SYSTEM'}},
        default_swarm_name='sw',
    )
    chunk = _normalize('system_tool_chunk', {'text': 'abc', 'progress': 0.3}, state=state)
    no_text = _normalize('system_tool_chunk', {'tool_id': 'x'}, state=state)
    complete = _normalize(
        'system_tool_complete',
        {
            'tool_name': 'web_search',
            'tool_id': 'call-1',
            'outcome': {'result': {'r': 1}, 'duration_ms': 4},
            'latency_ms': -1,
        },
        state=state,
    )

    assert start[0]['tool'] == {
        'id': 'call-1',
        'name': 'web_search',
        'type': 'system',
        'status': 'executing',
        'args': {'q': 'x'},
        'private_data_keys': ['k'],
    }
    assert (start[0]['agent_name'], start[0]['swarm_name']) == ('ag', 'sw')
    assert [(event['tool_id'], event['text'], event['progress']) for event in chunk] == [
        ('call-1', 'abc', 0.3)
    ]
    assert no_text == []
    assert complete[0]['tool'] == {
        'id': 'call-1',
        'name': 'web_search',
        'type': 'system',
        'status': 'completed',
        'args': {},
        'result': {'r': 1},
        'duration_ms': 4,
        'private_data_keys': [],
    }
    assert state.last_tool_result == {'r': 1}
    assert state.started_system_tools == {}


def test_registered_tool_metadata_names_and_classifies_the_start() -> None:
    """A ``spec_ref`` tool id selects metadata for name, type, args and private keys."""
    (start,) = _normalize(
        'system_tool_start',
        {'tool_call': {'spec_ref': {'tool_id': 'reg-1'}, 'arguments': {'a': 1}}},
        tool_metadata_map={
            'reg-1': {'tool_name': 'Registered', 'tool_type': 'mcp', 'private_data_keys': ['p']}
        },
    )
    (plain,) = _normalize('system_tool_start', {'tool_call': {'args': {'b': 2}}})

    assert (start['tool_name'], start['tool_type'], start['args'], start['private_data_keys']) == (
        'Registered',
        'mcp',
        {'a': 1},
        ['p'],
    )
    assert uuid.UUID(str(start['tool_id'])).version == 4  # noqa: PLR2004
    assert (plain['tool_name'], plain['tool_type'], plain['args']) == (
        'system_tool',
        'func',
        {'b': 2},
    )


def test_chunk_without_id_and_several_started_tools_uses_placeholder() -> None:
    """With zero or several started tools, an id-less chunk falls back to ``system_tool``."""
    state = NormalizedStreamState()
    _ = _normalize('system_tool_start', {'tool_name': 'a', 'tool_id': 'a1'}, state=state)
    _ = _normalize('system_tool_start', {'tool_name': 'b', 'tool_id': 'b1'}, state=state)

    (chunk,) = _normalize('system_tool_chunk', {'text': 'x', 'progress': 'half'}, state=state)

    assert (chunk['tool_id'], chunk.get('progress')) == ('system_tool', None)


def test_system_tool_errors_surface_the_best_available_message() -> None:
    """Errors fall back from the payload to the outcome message, code and ``error_code``."""
    by_code = _normalize(
        'system_tool_error',
        {
            'correlation_id': 'c1',
            'outcome': {'error': {'error_code': 'quota'}},
            'duration_ms': True,
        },
    )
    by_message = _normalize(
        'system_tool_error',
        {'outcome': {'call_id': 'oc', 'error': {'message': 'msg', 'code': 'c'}}},
    )
    by_outcome_text = _normalize(
        'system_tool_error', {'tool_id': 't', 'outcome': {'error': 'text'}}
    )
    by_plain_code = _normalize(
        'system_tool_error', {'assignment_id': 'as', 'outcome': {'error': {'code': 'c'}}}
    )
    nothing = _normalize('system_tool_error', {'latency_ms': 7})

    assert [(event['tool_id'], event['error']) for event in by_code] == [('c1', 'quota')]
    assert 'duration_ms' not in by_code[0]
    assert [(event['tool_id'], event['error']) for event in by_message] == [('oc', 'msg')]
    assert [(event['tool_id'], event['error']) for event in by_outcome_text] == [('t', 'text')]
    assert [(event['tool_id'], event['error']) for event in by_plain_code] == [('as', 'c')]
    assert [(event['tool_id'], event['error'], event['duration_ms']) for event in nothing] == [
        ('system_tool', 'Unknown error', 7)
    ]


# MARK: Lifecycle Events


def test_enrichment_requires_a_phase_and_derives_messages() -> None:
    """Phase-less enrichment is dropped; known phases get their canonical message."""
    assert _normalize('enrichment', {'message': 'm'}) == []

    (known,) = _normalize('enrichment', {'phase': 'evaluating', 'scope_id': 's', 'memory': {}})

    assert known['enrichment'] == {'phase': 'evaluating', 'message': 'Evaluating request...'}
    assert known['scope'] == {'id': 's'}


def test_interrupts_project_canonical_kinds_or_legacy_fields() -> None:
    """Canonical interrupt kinds become durable brain events; others use legacy fields."""
    (direct,) = _normalize(
        'interrupt_required', {'interrupt': {'kind': 'tool_approval', 'id': 'x'}}
    )
    (nested,) = _normalize(
        'interrupt_required',
        {'stream_part': {'data': {'interrupt': {'kind': 'agent_followup', 'q': 1}}}},
    )
    (legacy,) = _normalize(
        'interrupt_required',
        {
            'interrupt_id': 'i',
            'data_key': 'k',
            'prompt': 'p',
            'interrupt_number': 2,
            'total_interrupts': 'x',
            'choices': [1, 'b'],
            'input_type': 'choice',
            'timestamp': 't',
            'interrupt': {'kind': 'other'},
        },
    )
    (minimal,) = _normalize('interrupt_required', {})

    assert (direct['event_name'], direct['interrupt']) == (
        'brain.session.interrupted',
        {'id': 'x', 'kind': 'tool_approval', 'choices': []},
    )
    assert direct['payload'] == {'interrupt': {'kind': 'tool_approval', 'id': 'x'}}
    assert nested['interrupt'] == {'kind': 'agent_followup', 'q': 1, 'choices': []}
    assert legacy['interrupt'] == {
        'id': 'i',
        'data_key': 'k',
        'arg_name': 'k',
        'prompt': 'p',
        'input_type': 'choice',
        'choices': ['1', 'b'],
        'number': 2,
        'total': 1,
    }
    assert legacy['timestamp'] == 't'
    assert (minimal['data_key'], minimal['prompt']) == ('input', 'Input required.')
    assert uuid.UUID(str(minimal['interrupt_id'])).version == 4  # noqa: PLR2004


def test_error_events_merge_details_with_diagnostic_facts() -> None:
    """Error text is trimmed and diagnostic facts join the details."""
    (error,) = _normalize(
        'error',
        {'error': ' bad ', 'details': {'d': 1}, 'error_code': 'validation_failed'},
    )
    (empty,) = _normalize('error', {})

    assert error['error_info'] == {
        'message': 'bad',
        'code': 'validation_failed',
        'details': {'d': 1, 'error_code': 'validation_failed'},
    }
    assert empty['error_info'] == {'message': 'Unknown error', 'details': {}}


def test_session_start_needs_both_ids_and_hooks_need_names() -> None:
    """Session starts and hook firings are dropped when identifying fields are missing."""
    (session,) = _normalize('session_start', {'session_id': 's', 'assistant_id': 'a'})
    (hook,) = _normalize(
        'hook_fired',
        {
            'hook': {
                'name': 'h',
                'stage': 'before',
                'target_type': 'tool',
                'error': 'bad',
                'elapsed_ms': -1,
            },
            'elapsed_ms': True,
        },
    )
    (completed,) = _normalize(
        'hook_fired',
        {'name': 'h', 'stage': 'after', 'target_type': 'agent', 'hook': {'elapsed_ms': 6}},
    )

    assert session['session'] == {'id': 's', 'assistant_id': 'a'}
    assert _normalize('session_start', {'session_id': 's'}) == []
    assert hook['hook'] == {
        'name': 'h',
        'stage': 'before',
        'status': 'failed',
        'target_type': 'tool',
        'error': 'bad',
    }
    assert completed['hook'] == {
        'name': 'h',
        'stage': 'after',
        'status': 'completed',
        'target_type': 'agent',
        'elapsed_ms': 6,
    }
    assert _normalize('hook_fired', {'name': 'h'}) == []


# MARK: Stream Entry Points


def test_v1_payloads_pass_through_and_gain_private_field_names() -> None:
    """Already-normalized tool events are validated as-is plus metadata private keys."""
    payload: dict[str, JsonValue] = {
        'contract_version': 'v1',
        'event_name': 'tool_event',
        'tool_id': 't',
        'tool': {'name': 'lookup'},
    }

    (enriched,) = _normalize(
        'anything', payload, tool_metadata_map={'lookup': {'private_data_keys': ['ssn', '', 3]}}
    )
    (unchanged,) = _normalize('anything', payload)
    (kept,) = _normalize(
        'anything',
        {**payload, 'private_data_keys': ['own']},
        tool_metadata_map={'lookup': {'private_data_keys': ['ssn']}},
    )
    (no_keys,) = _normalize(
        'anything', payload, tool_metadata_map={'lookup': {'private_data_keys': ['', 3]}}
    )
    (not_list,) = _normalize(
        'anything', payload, tool_metadata_map={'t': {'private_data_keys': 'ssn'}}
    )
    (other_event,) = _normalize(
        'anything',
        {'contract_version': 'v1', 'event_name': 'final', 'response': 'r'},
        tool_metadata_map={'lookup': {'private_data_keys': ['ssn']}},
    )

    assert (enriched['private_data_keys'], enriched['tool']) == (
        ['ssn'],
        {'name': 'lookup', 'args': {}, 'private_data_keys': ['ssn']},
    )
    assert 'private_data_keys' not in unchanged
    assert kept['private_data_keys'] == ['own']
    assert 'private_data_keys' not in no_keys
    assert 'private_data_keys' not in not_list
    assert other_event['response'] == 'r'


def test_unknown_names_and_wrong_contract_versions_are_dropped() -> None:
    """Events without a handler produce nothing, even when they look versioned."""
    assert _normalize('mystery', {'x': 1}) == []
    assert _normalize('mystery', {'contract_version': 'v0', 'event_name': 'final'}) == []
    assert _normalize('mystery', {'contract_version': 'v1'}) == []


def test_normalize_stream_shares_state_across_events() -> None:
    """The stream helper threads one state so deltas and pending tools carry over."""
    events = list(
        normalize_stream(
            [
                RawSSEEvent(name='update', payload={'streaming_content': 'Hel'}),
                RawSSEEvent(name='mystery', payload={}),
                RawSSEEvent(name='update', payload={'streaming_content': 'Hello'}),
                RawSSEEvent(
                    name='model_tool_complete', payload={'event_id': 'm', 'result': {'a': 1}}
                ),
                RawSSEEvent(name='final', payload={'response': 'done'}),
            ],
            default_agent_name='Agent',
        )
    )

    assert all(isinstance(event, AppEvent) for event in events)
    assert [
        (event.event_name, (event.assistant.delta if event.assistant else None)) for event in events
    ] == [
        ('assistant_chunk', 'Hel'),
        ('assistant_chunk', 'lo'),
        ('tool_event', None),
        ('tool_event', None),
        ('final', None),
    ]
    assert events[3].tool is not None
    assert (events[3].tool.status, events[3].tool.result) == ('completed', {'a': 1})
    assert events[4].output is not None
    assert events[4].output.result == {'a': 1}


def test_final_response_selection_skips_blank_and_non_text_responses() -> None:
    """Blank or non-text ``responses`` fall back to ``response`` or an empty answer."""
    (blank,) = _normalize('final', {'responses': ['  '], 'response': ' kept '})
    (non_text,) = _normalize('final', {'responses': [' ', 3], 'result': 1})

    assert (blank['response'], blank['responses']) == ('kept', ['kept'])
    assert (non_text['response'], non_text['result']) == ('', 1)
    assert 'private_value_restorations' not in non_text


def test_agent_tool_without_metadata_keeps_its_own_args() -> None:
    """An agent-typed call with no metadata entry keeps its arguments untouched."""
    (event,) = _normalize(
        'tool_event',
        {
            'value': {
                'tool_calls': [{'id': 'a3', 'name': 'Helper', 'type': 'agent', 'args': {'x': 1}}]
            }
        },
        tool_metadata_map={'other': {'target_agent_id': 'meta'}},
    )

    assert (event['tool_type'], event['agent_name'], event['args']) == ('agent', 'Helper', {'x': 1})


def test_pending_replace_flag_sends_the_full_snapshot() -> None:
    """With the replace flag set, a continuing snapshot is sent whole, not as a suffix."""
    state = NormalizedStreamState(
        streaming_text_by_id={'a': 'Hel'},
        next_assistant_chunk_replaces=True,
    )

    (event,) = _normalize(
        'update', {'streaming_content': 'Hello', 'assistant_id': 'a'}, state=state
    )

    assert (event['text'], event['replace_content']) == ('Hello', True)


def test_final_uses_the_last_non_blank_response() -> None:
    """Among several responses the latest non-blank one is the answer."""
    (final,) = _normalize('final', {'responses': ['first', 'second', ' ']})

    assert (final['response'], final['responses']) == ('second', ['second'])
