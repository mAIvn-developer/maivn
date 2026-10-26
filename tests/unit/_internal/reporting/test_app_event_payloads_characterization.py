"""Characterization tests for the AppEvent payload builders.

The builders are public through ``maivn.events`` and Studio consumes the dicts
they return, so every test pins the complete payload, including the shared
``contract_version`` / ``event_name`` / ``event_kind`` / ``scope`` /
``participant`` envelope fields.
"""

from __future__ import annotations

import pytest

from maivn.events import (
    APP_EVENT_CONTRACT_VERSION,
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_hook_fired_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_status_message_chunk_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
)


def test_contract_version_is_v1() -> None:
    """The AppEvent contract version stamped on every payload is ``v1``."""
    assert APP_EVENT_CONTRACT_VERSION == 'v1'


# Section: Assistant and status payloads.


def test_assistant_chunk_without_participant_or_restorations() -> None:
    """A plain chunk carries null participant fields and no participant envelope."""
    assert build_assistant_chunk_payload(assistant_id='asst-1', text='Hel') == {
        'assistant_id': 'asst-1',
        'text': 'Hel',
        'participant_key': None,
        'participant_name': None,
        'participant_role': None,
        'assistant': {'id': 'asst-1', 'delta': 'Hel'},
        'contract_version': 'v1',
        'event_name': 'assistant_chunk',
        'event_kind': 'assistant',
    }


def test_assistant_chunk_with_participant_replacement_and_restorations() -> None:
    """Participant fields are trimmed, and replacement and restorations are flagged."""
    restorations: list[dict[str, object]] = [{'path': '/text', 'start': 0, 'end': 3}]

    payload = build_assistant_chunk_payload(
        assistant_id='asst-1',
        text='New',
        participant_key=' agent-1 ',
        participant_name='Planner',
        participant_role='  ',
        replace_content=True,
        private_value_restorations=restorations,  # pyright: ignore[reportArgumentType]
    )

    assert payload == {
        'assistant_id': 'asst-1',
        'text': 'New',
        'participant_key': 'agent-1',
        'participant_name': 'Planner',
        'participant_role': None,
        'assistant': {'id': 'asst-1', 'delta': 'New', 'replace_content': True},
        'replace_content': True,
        'private_value_restorations': restorations,
        'contract_version': 'v1',
        'event_name': 'assistant_chunk',
        'event_kind': 'assistant',
        'participant': {'key': 'agent-1', 'name': 'Planner'},
    }


def test_assistant_chunk_with_role_only_participant() -> None:
    """A role alone still produces a participant envelope."""
    payload = build_assistant_chunk_payload(
        assistant_id='asst-1', text='x', participant_role='reviewer', private_value_restorations=[]
    )

    assert payload['participant'] == {'role': 'reviewer'}
    assert payload['participant_role'] == 'reviewer'
    assert 'private_value_restorations' not in payload
    assert 'replace_content' not in payload


def test_status_message_payload() -> None:
    """A status line nests its message under ``status``."""
    assert build_status_message_payload(assistant_id='asst-1', message='Searching') == {
        'assistant_id': 'asst-1',
        'message': 'Searching',
        'assistant': {'id': 'asst-1'},
        'status': {'message': 'Searching'},
        'contract_version': 'v1',
        'event_name': 'status_message',
        'event_kind': 'status',
    }


def test_status_message_chunk_payload_in_progress() -> None:
    """An in-progress status chunk has no top-level ``final`` flag."""
    assert build_status_message_chunk_payload(
        assistant_id='asst-1', status_id='st-1', text='Sea'
    ) == {
        'assistant_id': 'asst-1',
        'status_id': 'st-1',
        'text': 'Sea',
        'assistant': {'id': 'asst-1'},
        'status': {'id': 'st-1', 'delta': 'Sea', 'final': False},
        'contract_version': 'v1',
        'event_name': 'status_message_chunk',
        'event_kind': 'status',
    }


def test_status_message_chunk_payload_final() -> None:
    """The final status chunk is flagged at both levels."""
    assert build_status_message_chunk_payload(
        assistant_id='asst-1', status_id='st-1', text='rch', final=True
    ) == {
        'assistant_id': 'asst-1',
        'status_id': 'st-1',
        'text': 'rch',
        'assistant': {'id': 'asst-1'},
        'status': {'id': 'st-1', 'delta': 'rch', 'final': True},
        'final': True,
        'contract_version': 'v1',
        'event_name': 'status_message_chunk',
        'event_kind': 'status',
    }


# Section: Interrupt and assignment payloads.


def test_interrupt_payload_defaults() -> None:
    """Missing interrupt metadata defaults to text input, one of one, and the data key."""
    assert build_interrupt_required_payload(
        interrupt_id='int-1', data_key='account', prompt='Account?'
    ) == {
        'interrupt_id': 'int-1',
        'checkpoint_id': '',
        'data_key': 'account',
        'prompt': 'Account?',
        'tool_name': '',
        'arg_name': 'account',
        'assignment_id': '',
        'assignment_index': 0,
        'interrupt_number': 1,
        'total_interrupts': 1,
        'input_type': 'text',
        'choices': [],
        'timestamp': None,
        'interrupt': {
            'id': 'int-1',
            'checkpoint_id': None,
            'assignment_id': None,
            'data_key': 'account',
            'arg_name': 'account',
            'prompt': 'Account?',
            'tool_name': None,
            'input_type': 'text',
            'choices': [],
            'number': 1,
            'total': 1,
        },
        'contract_version': 'v1',
        'event_name': 'interrupt_required',
        'event_kind': 'interrupt',
    }


def test_interrupt_payload_with_every_field() -> None:
    """Supplied interrupt metadata is carried at both levels."""
    payload = build_interrupt_required_payload(
        interrupt_id='int-2',
        data_key='plan',
        prompt='Pick one',
        tool_name='planner',
        arg_name='choice',
        checkpoint_id='cp-1',
        assignment_id='as-1',
        interrupt_number=2,
        total_interrupts=3,
        input_type='choice',
        choices=['a', 'b'],
        timestamp='2026-07-12T00:00:00Z',
    )

    assert payload == {
        'interrupt_id': 'int-2',
        'checkpoint_id': 'cp-1',
        'data_key': 'plan',
        'prompt': 'Pick one',
        'tool_name': 'planner',
        'arg_name': 'choice',
        'assignment_id': 'as-1',
        'assignment_index': 0,
        'interrupt_number': 2,
        'total_interrupts': 3,
        'input_type': 'choice',
        'choices': ['a', 'b'],
        'timestamp': '2026-07-12T00:00:00Z',
        'interrupt': {
            'id': 'int-2',
            'checkpoint_id': 'cp-1',
            'assignment_id': 'as-1',
            'data_key': 'plan',
            'arg_name': 'choice',
            'prompt': 'Pick one',
            'tool_name': 'planner',
            'input_type': 'choice',
            'choices': ['a', 'b'],
            'number': 2,
            'total': 3,
        },
        'contract_version': 'v1',
        'event_name': 'interrupt_required',
        'event_kind': 'interrupt',
    }


def test_agent_assignment_payload_inside_a_swarm() -> None:
    """A swarm assignment carries a swarm scope and a trimmed swarm name."""
    payload = build_agent_assignment_payload(
        agent_name='Planner',
        status='completed',
        assignment_id='as-1',
        swarm_name=' Crew ',
        task='Plan',
        result={'ok': True},
        use_as_final_output=True,
        participant_key='p-1',
    )

    assert payload == {
        'assignment_id': 'as-1',
        'agent_name': 'Planner',
        'status': 'completed',
        'task': 'Plan',
        'swarm_name': 'Crew',
        'result': {'ok': True},
        'error': None,
        'use_as_final_output': True,
        'participant_key': 'p-1',
        'participant_name': None,
        'participant_role': None,
        'assignment': {
            'id': 'as-1',
            'agent_name': 'Planner',
            'status': 'completed',
            'task': 'Plan',
            'swarm_name': 'Crew',
            'result': {'ok': True},
            'error': None,
            'use_as_final_output': True,
        },
        'lifecycle': {'phase': 'completed'},
        'contract_version': 'v1',
        'event_name': 'agent_assignment',
        'event_kind': 'assignment',
        'scope': {'type': 'swarm', 'name': 'Crew'},
        'participant': {'key': 'p-1'},
    }


def test_agent_assignment_payload_without_a_swarm() -> None:
    """An assignment outside a swarm has no scope or participant envelope."""
    payload = build_agent_assignment_payload(agent_name='Solo', status='failed', error='boom')

    assert payload == {
        'assignment_id': None,
        'agent_name': 'Solo',
        'status': 'failed',
        'task': None,
        'swarm_name': None,
        'result': None,
        'error': 'boom',
        'use_as_final_output': False,
        'participant_key': None,
        'participant_name': None,
        'participant_role': None,
        'assignment': {
            'id': None,
            'agent_name': 'Solo',
            'status': 'failed',
            'task': None,
            'swarm_name': None,
            'result': None,
            'error': 'boom',
            'use_as_final_output': False,
        },
        'lifecycle': {'phase': 'failed'},
        'contract_version': 'v1',
        'event_name': 'agent_assignment',
        'event_kind': 'assignment',
    }


# Section: Hook payloads.


def test_hook_fired_payload() -> None:
    """A hook firing mirrors every field under ``hook``."""
    fields: dict[str, object] = {
        'name': 'audit',
        'stage': 'after',
        'status': 'failed',
        'target_type': 'tool',
        'target_id': 'e-1',
        'target_name': 'search',
        'source': 'scope',
        'error': 'hook raised',
        'elapsed_ms': 4,
    }

    payload = build_hook_fired_payload(
        name='audit',
        stage='after',
        status='failed',
        target_type='tool',
        target_id='e-1',
        target_name='search',
        source='scope',
        error='hook raised',
        elapsed_ms=4,
    )

    assert payload == {
        **fields,
        'hook': fields,
        'contract_version': 'v1',
        'event_name': 'hook_fired',
        'event_kind': 'hook',
    }


# Section: Enrichment payloads.


def test_enrichment_payload_minimal() -> None:
    """A bare enrichment milestone has no scope, metrics, or participant."""
    assert build_enrichment_payload(phase='planning', message='Planning') == {
        'phase': 'planning',
        'message': 'Planning',
        'enrichment': {'phase': 'planning', 'message': 'Planning'},
        'contract_version': 'v1',
        'event_name': 'enrichment',
        'event_kind': 'enrichment',
    }


def test_enrichment_payload_with_every_section() -> None:
    """Scope, metrics, reevaluate attribution, and participant are all projected."""
    reevaluate: dict[str, object] = {
        'source': 'tool',
        'trigger_tool': 'search',
        'target_tool': '',
        'reevaluate_count': 2,
        'collected_count': 'many',
    }

    payload = build_enrichment_payload(
        phase='reevaluate_accrued',
        message='Collected',
        scope_id=' ag-1 ',
        scope_name='Planner',
        scope_type='AGENT',
        memory={'hits': 1},
        redaction={'masked': 2},
        reevaluate=reevaluate,  # pyright: ignore[reportArgumentType]
        participant_key='p-1',
        participant_name='Planner',
        participant_role='lead',
    )

    assert payload == {
        'phase': 'reevaluate_accrued',
        'message': 'Collected',
        'enrichment': {
            'phase': 'reevaluate_accrued',
            'message': 'Collected',
            'memory': {'hits': 1},
            'redaction': {'masked': 2},
            'reevaluate': reevaluate,
        },
        'scope_id': 'ag-1',
        'scope_name': 'Planner',
        'scope_type': 'agent',
        'memory': {'hits': 1},
        'redaction': {'masked': 2},
        'reevaluate': reevaluate,
        'source': 'tool',
        'trigger_tool': 'search',
        'reevaluate_count': 2,
        'participant_key': 'p-1',
        'participant_name': 'Planner',
        'participant_role': 'lead',
        'contract_version': 'v1',
        'event_name': 'enrichment',
        'event_kind': 'enrichment',
        'scope': {'type': 'agent', 'id': 'ag-1', 'name': 'Planner'},
        'participant': {'key': 'p-1', 'name': 'Planner', 'role': 'lead'},
    }


@pytest.mark.parametrize(
    ('participant', 'flat'),
    [
        ({'participant_key': 'p-1'}, {'participant_key': 'p-1'}),
        ({'participant_name': 'Planner'}, {'participant_name': 'Planner'}),
        ({'participant_role': 'lead'}, {'participant_role': 'lead'}),
    ],
)
def test_enrichment_payload_projects_only_supplied_participant_fields(
    participant: dict[str, str], flat: dict[str, str]
) -> None:
    """Only supplied participant fields are flattened onto the payload."""
    payload = build_enrichment_payload(
        phase='p',
        message='m',
        participant_key=participant.get('participant_key'),
        participant_name=participant.get('participant_name'),
        participant_role=participant.get('participant_role'),
    )

    assert payload == {
        'phase': 'p',
        'message': 'm',
        'enrichment': {'phase': 'p', 'message': 'm'},
        **flat,
        'contract_version': 'v1',
        'event_name': 'enrichment',
        'event_kind': 'enrichment',
        'participant': {key.removeprefix('participant_'): value for key, value in flat.items()},
    }


def test_enrichment_payload_copies_metric_dicts() -> None:
    """Metric dicts are copied, so later caller mutation does not leak in."""
    memory: dict[str, object] = {'hits': 1}

    payload = build_enrichment_payload(phase='memory', message='m', memory=memory)  # pyright: ignore[reportArgumentType]
    memory['hits'] = 99

    assert payload['memory'] == {'hits': 1}


def test_enrichment_payload_elides_empty_metric_dicts() -> None:
    """Empty metric dicts are treated like missing ones."""
    payload = build_enrichment_payload(
        phase='memory', message='m', memory={}, redaction={}, reevaluate={}
    )

    assert payload == {
        'phase': 'memory',
        'message': 'm',
        'enrichment': {'phase': 'memory', 'message': 'm'},
        'contract_version': 'v1',
        'event_name': 'enrichment',
        'event_kind': 'enrichment',
    }


@pytest.mark.parametrize('scope_type', ['tool', '   '])
def test_enrichment_payload_drops_an_unknown_scope_type(scope_type: str) -> None:
    """Only agent, swarm, and session scope types survive normalization."""
    payload = build_enrichment_payload(
        phase='p', message='m', scope_type=scope_type, scope_name='Planner'
    )

    assert payload['scope'] == {'name': 'Planner'}
    assert payload['scope_name'] == 'Planner'
    assert 'scope_type' not in payload


def test_enrichment_payload_with_only_a_scope_id() -> None:
    """A lone scope id produces an id-only scope."""
    payload = build_enrichment_payload(
        phase='p', message='m', scope_id='ses-1', scope_type='session'
    )

    assert payload['scope'] == {'type': 'session', 'id': 'ses-1'}
    assert (payload['scope_id'], payload['scope_type']) == ('ses-1', 'session')
    assert 'scope_name' not in payload


# Section: Terminal and session payloads.


def test_final_payload_with_response() -> None:
    """A final payload lists the response and mirrors it under ``output``."""
    usage: dict[str, object] = {'total_tokens': 3}

    payload = build_final_payload(response='Done', result={'a': 1}, token_usage=usage)  # pyright: ignore[reportArgumentType]

    assert payload == {
        'responses': ['Done'],
        'response': 'Done',
        'result': {'a': 1},
        'token_usage': usage,
        'output': {'response': 'Done', 'result': {'a': 1}, 'token_usage': usage},
        'contract_version': 'v1',
        'event_name': 'final',
        'event_kind': 'final',
    }


def test_final_payload_with_blank_response() -> None:
    """A whitespace-only response produces an empty response list."""
    assert build_final_payload(response='  ') == {
        'responses': [],
        'response': '  ',
        'result': None,
        'token_usage': None,
        'output': {'response': '  ', 'result': None, 'token_usage': None},
        'contract_version': 'v1',
        'event_name': 'final',
        'event_kind': 'final',
    }


def test_error_payload_with_an_error_code() -> None:
    """A string ``error_code`` detail becomes the error info code."""
    details: dict[str, object] = {'error_code': 'permission_denied', 'attempt': 2}

    payload = build_error_payload(error='Denied', details=details)  # pyright: ignore[reportArgumentType]

    assert payload == {
        'error': 'Denied',
        'details': details,
        'error_info': {'message': 'Denied', 'details': details, 'code': 'permission_denied'},
        'contract_version': 'v1',
        'event_name': 'error',
        'event_kind': 'error',
    }


def test_error_payload_without_details_or_with_a_non_string_code() -> None:
    """Missing details become ``{}`` and a non-string code is not promoted."""
    assert build_error_payload(error='Boom') == {
        'error': 'Boom',
        'details': {},
        'error_info': {'message': 'Boom', 'details': {}},
        'contract_version': 'v1',
        'event_name': 'error',
        'event_kind': 'error',
    }
    numeric = build_error_payload(error='Boom', details={'error_code': 500})
    assert numeric['error_info'] == {'message': 'Boom', 'details': {'error_code': 500}}


def test_session_start_payload() -> None:
    """Session start anchors a session scope on the session id."""
    assert build_session_start_payload(session_id='ses-1', assistant_id='asst-1') == {
        'session_id': 'ses-1',
        'assistant_id': 'asst-1',
        'session': {'id': 'ses-1', 'assistant_id': 'asst-1'},
        'contract_version': 'v1',
        'event_name': 'session_start',
        'event_kind': 'session',
        'scope': {'type': 'session', 'id': 'ses-1'},
    }


# Section: Tool payloads.


def test_tool_event_payload_defaults() -> None:
    """A bare tool event defaults to a ``func`` tool with empty args and keys."""
    assert build_tool_event_payload(tool_name='search', tool_id='e-1', status='executing') == {
        'tool_name': 'search',
        'tool_id': 'e-1',
        'tool_type': 'func',
        'status': 'executing',
        'args': {},
        'result': None,
        'error': None,
        'duration_ms': None,
        'private_data_keys': [],
        'agent_name': None,
        'swarm_name': None,
        'participant_key': None,
        'participant_name': None,
        'participant_role': None,
        'tool': {
            'id': 'e-1',
            'name': 'search',
            'type': 'func',
            'status': 'executing',
            'args': {},
            'result': None,
            'error': None,
            'duration_ms': None,
            'private_data_keys': [],
        },
        'lifecycle': {'phase': 'executing'},
        'contract_version': 'v1',
        'event_name': 'tool_event',
        'event_kind': 'tool',
    }


def test_tool_event_payload_with_agent_scope_and_participant() -> None:
    """An agent-scoped completion carries its scope, keys, and participant."""
    payload = build_tool_event_payload(
        tool_name='search',
        tool_id='e-1',
        status='completed',
        args={'q': 'x'},
        result={'rows': 1},
        agent_name=' Planner ',
        tool_type=' mcp ',
        duration_ms=12,
        private_data_keys=['acct'],
        participant_name='Planner',
    )

    assert payload == {
        'tool_name': 'search',
        'tool_id': 'e-1',
        'tool_type': 'mcp',
        'status': 'completed',
        'args': {'q': 'x'},
        'result': {'rows': 1},
        'error': None,
        'duration_ms': 12,
        'private_data_keys': ['acct'],
        'agent_name': 'Planner',
        'swarm_name': None,
        'participant_key': None,
        'participant_name': 'Planner',
        'participant_role': None,
        'tool': {
            'id': 'e-1',
            'name': 'search',
            'type': 'mcp',
            'status': 'completed',
            'args': {'q': 'x'},
            'result': {'rows': 1},
            'error': None,
            'duration_ms': 12,
            'private_data_keys': ['acct'],
        },
        'lifecycle': {'phase': 'completed'},
        'contract_version': 'v1',
        'event_name': 'tool_event',
        'event_kind': 'tool',
        'scope': {'type': 'agent', 'name': 'Planner'},
        'participant': {'name': 'Planner'},
    }


def test_tool_event_scope_prefers_the_swarm_and_ignores_non_dict_args() -> None:
    """A swarm name wins over an agent name, and non-dict args become ``{}``."""
    payload = build_tool_event_payload(
        tool_name='search',
        tool_id='e-1',
        status='failed',
        args=['not', 'a', 'dict'],
        error='timeout',
        agent_name='Planner',
        swarm_name='Crew',
    )

    assert payload['scope'] == {'type': 'swarm', 'name': 'Crew'}
    assert (payload['agent_name'], payload['swarm_name']) == ('Planner', 'Crew')
    assert payload['args'] == {}
    assert payload['error'] == 'timeout'


def test_system_tool_start_payload() -> None:
    """A system tool start uses its type as the tool name and copies params."""
    params: dict[str, object] = {'query': 'x'}

    payload = build_system_tool_start_payload(
        tool_type='web_search', tool_id='st-1', params=params, swarm_name='Crew'
    )
    params['query'] = 'changed'

    assert payload == {
        'tool_type': 'web_search',
        'tool_id': 'st-1',
        'params': {'query': 'x'},
        'agent_name': None,
        'swarm_name': 'Crew',
        'tool': {
            'id': 'st-1',
            'name': 'web_search',
            'type': 'system',
            'status': 'executing',
            'args': {'query': 'x'},
        },
        'lifecycle': {'phase': 'executing'},
        'contract_version': 'v1',
        'event_name': 'system_tool_start',
        'event_kind': 'tool',
        'scope': {'type': 'swarm', 'name': 'Crew'},
    }


def test_system_tool_start_payload_without_scope() -> None:
    """A system tool start without agent or swarm has no scope envelope."""
    payload = build_system_tool_start_payload(tool_type='think', tool_id='st-2')

    assert payload['params'] == {}
    assert 'scope' not in payload


def test_system_tool_chunk_payload() -> None:
    """A system tool chunk carries its text and progress fraction."""
    assert build_system_tool_chunk_payload(tool_id='st-1', text='found', progress=0.5) == {
        'tool_id': 'st-1',
        'text': 'found',
        'progress': 0.5,
        'tool': {'id': 'st-1', 'type': 'system'},
        'chunk': {'text': 'found', 'progress': 0.5},
        'contract_version': 'v1',
        'event_name': 'system_tool_chunk',
        'event_kind': 'tool',
    }


def test_system_tool_complete_payload() -> None:
    """A system tool completion carries its result and duration."""
    assert build_system_tool_complete_payload(tool_id='st-1', result='done', duration_ms=7) == {
        'tool_id': 'st-1',
        'result': 'done',
        'duration_ms': 7,
        'tool': {
            'id': 'st-1',
            'type': 'system',
            'status': 'completed',
            'result': 'done',
            'duration_ms': 7,
        },
        'lifecycle': {'phase': 'completed'},
        'contract_version': 'v1',
        'event_name': 'system_tool_complete',
        'event_kind': 'tool',
    }
