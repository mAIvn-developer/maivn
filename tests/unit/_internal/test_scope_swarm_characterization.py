"""Characterization tests for the public Swarm scope and the requests it sends."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import (
    Agent,
    ApprovalDecision,
    Client,
    ClientConfig,
    InvokeResponse,
    RunOptions,
    Skill,
    SkillSet,
    SkillStep,
    StreamEvent,
    Swarm,
    ThreadAccepted,
    ThreadState,
)

if TYPE_CHECKING:
    from pathlib import Path

JsonObject = dict[str, Any]
_ACCEPTED: JsonObject = {'thread_id': 'thr-1', 'session_id': 'ses-1', 'stream_position': 0}
_THREAD_STATE: JsonObject = {
    'thread_id': 'thr-1',
    'status': 'idle',
    'created_at': '2026-01-01T00:00:00Z',
    'updated_at': '2026-01-01T00:00:00Z',
}


def _final_sse(session_id: str, content: str = 'done') -> httpx.Response:
    event: JsonObject = {
        'event_id': f'evt-{session_id}',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': session_id,
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': f'msg-{session_id}',
                'role': 'assistant',
                'content': content,
                'ts': '2026-07-04T12:00:00Z',
            },
            'usage': {},
            'tool_calls': {'count': 0, 'names': []},
        },
        'ts': '2026-07-04T12:00:00Z',
    }
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: 1\nevent: final\ndata: {json.dumps(event)}\n\n',
    )


class _Server:
    """Offline API double that records every request the SDK sends."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, JsonObject | None]] = []
        self.skill_ids: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - one route each.
        body = cast('JsonObject', json.loads(request.content)) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        if path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-invoke', 'stream_position': 0}
            )
        if request.method == 'POST' and path.startswith('/v1/threads'):
            return httpx.Response(HTTPStatus.ACCEPTED, json=_ACCEPTED)
        if path.endswith('/events'):
            return _final_sse('ses-1')
        if path == '/v1/threads/thr-1':
            return httpx.Response(HTTPStatus.OK, json=_THREAD_STATE)
        if path == '/v1/skills':
            assert body is not None
            memory_id = f'skill-{len(self.skill_ids) + 1}'
            self.skill_ids.append(memory_id)
            return httpx.Response(
                HTTPStatus.OK, json={**body, 'kind': 'skill', 'memory_id': memory_id}
            )
        if path == '/v1/skill-sets':
            assert body is not None
            return httpx.Response(HTTPStatus.OK, json={**body, 'kind': 'skill_set', 'id': 'set-1'})
        if path.startswith('/v1/skill-sets/'):
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    'kind': 'skill_set',
                    'id': 'set-1',
                    'name': 'Linked',
                    'description': 'Linked.',
                },
            )
        return httpx.Response(HTTPStatus.NOT_FOUND, json={'detail': 'unexpected'})

    def client(self) -> Client:
        return Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
            transport=httpx.MockTransport(self.handle),
            private_data_store=None,
        )

    def bodies(self, path: str) -> list[JsonObject]:
        return [
            _stable(body)
            for _method, called, body in self.calls
            if called == path and body is not None
        ]


def _stable(body: JsonObject) -> JsonObject:
    """Replace per-request random message identities with stable markers."""
    stable = json.loads(json.dumps(body))
    for message in [
        *stable.get('messages', []),
        *([stable['message']] if 'message' in stable else []),
    ]:
        assert str(message.pop('message_id')).startswith('msg-')
        assert isinstance(message.pop('ts'), str)
    run_config = stable.get('run_config', {})
    if (
        str(run_config.get('thread_id', '')).startswith('thr-')
        and run_config['thread_id'] != 'thr-1'
    ):
        run_config['thread_id'] = '<generated>'
    return cast('JsonObject', stable)


def _swarm(server: _Server, **kwargs: Any) -> Swarm:
    member = Agent(name='member', client=server.client(), description='Member.')
    return Swarm(name='team', agents=[member], **kwargs)


_ROSTER: JsonObject = {
    'agents': [{'name': 'member', 'model': 'auto', 'description': 'Member.'}],
    'swarm_roster': True,
}


def _run_config(thread_id: str) -> JsonObject:
    return {
        'user_id': 'sdk-user',
        'thread_id': thread_id,
        'swarm_id': 'team',
        'model_directive': 'auto',
    }


def test_swarm_thread_lifecycle_requests_are_exact() -> None:
    """Thread start, message, rewind, approval, state, and events hit their exact routes."""
    server = _Server()
    swarm = _swarm(server, system_prompt='Coordinate.')

    started = swarm.start_thread('hi')
    posted = swarm.post_thread_message('thr-1', 'more')
    rewound = swarm.time_travel_thread(
        'thr-1', checkpoint_id='cp-1', message='redo', branch_from_message_id='msg-1'
    )
    approved = swarm.submit_approval(
        'thr-1', 'int-1', decision=ApprovalDecision(approved=True, decided_by='dana')
    )
    state = swarm.get_thread('thr-1')
    events = list(swarm.thread_events('thr-1'))

    assert started == posted == rewound == approved == ThreadAccepted(**_ACCEPTED)
    assert state == ThreadState.from_payload(_THREAD_STATE)
    assert [event.event_type for event in events] == ['final']
    assert server.bodies('/v1/threads') == [
        {
            'messages': [
                {'role': 'system', 'content': 'Coordinate.'},
                {'role': 'user', 'content': 'hi'},
            ],
            'run_config': _run_config('<generated>'),
            'model': 'auto',
            **_ROSTER,
        },
    ]
    assert server.bodies('/v1/threads/thr-1/messages') == [
        {
            'message': {'role': 'user', 'content': 'more'},
            'run_config': _run_config('thr-1'),
            'model': 'auto',
            **_ROSTER,
        },
    ]
    assert server.bodies('/v1/threads/thr-1/time-travel') == [
        {
            'checkpoint_id': 'cp-1',
            'message': {'role': 'user', 'content': 'redo'},
            'run_config': _run_config('thr-1'),
            'model': 'auto',
            **_ROSTER,
            'branch_from_message_id': 'msg-1',
        },
    ]
    assert server.bodies('/v1/threads/thr-1/approvals/int-1') == [
        {'approved': True, 'decided_by': 'dana'}
    ]
    assert [(method, path) for method, path, body in server.calls if body is None] == [
        ('GET', '/v1/threads/thr-1'),
        ('GET', '/v1/threads/thr-1/events'),
    ]


def test_swarm_async_thread_lifecycle_matches_sync_surface() -> None:
    """The async thread methods send the same requests and return the same records."""
    server = _Server()
    swarm = _swarm(server)

    async def run() -> tuple[
        list[ThreadAccepted], ThreadState, list[StreamEvent], list[StreamEvent]
    ]:
        accepted = [
            await swarm.astart_thread('hi', options=RunOptions(thread_id='thr-1')),
            await swarm.apost_thread_message('thr-1', 'more'),
            await swarm.atime_travel_thread('thr-1', checkpoint_id='cp-1', message='redo'),
            await swarm.asubmit_approval(
                'thr-1',
                'int-1',
                decision=ApprovalDecision(approved=False, decided_by='dana', reason='No.'),
            ),
        ]
        state = await swarm.aget_thread('thr-1')
        events = [event async for event in swarm.athread_events('thr-1')]
        rewound = [
            event
            async for event in swarm.atime_travel_thread_stream(
                'thr-1', checkpoint_id='cp-2', message='again', reasoning='low'
            )
        ]
        return accepted, state, events, rewound

    accepted, state, events, rewound = asyncio.run(run())

    assert accepted == [ThreadAccepted(**_ACCEPTED)] * 4
    assert state.thread_id == 'thr-1'
    assert [event.event_type for event in events] == ['final']
    assert [event.event_type for event in rewound] == ['final']
    # Only identity is pinned here: explicit RunOptions currently drop the scope's model
    # default, which is reported as a defect rather than locked in.
    started_config = server.bodies('/v1/threads')[0]['run_config']
    assert (started_config['thread_id'], started_config['swarm_id']) == ('thr-1', 'team')
    assert server.bodies('/v1/threads/thr-1/approvals/int-1') == [
        {'approved': False, 'decided_by': 'dana', 'reason': 'No.'},
    ]
    assert server.bodies('/v1/threads/thr-1/time-travel') == [
        {
            'checkpoint_id': 'cp-1',
            'message': {'role': 'user', 'content': 'redo'},
            'run_config': _run_config('thr-1'),
            'model': 'auto',
            **_ROSTER,
        },
        {
            'checkpoint_id': 'cp-2',
            'message': {'role': 'user', 'content': 'again'},
            'run_config': _run_config('thr-1'),
            'model': 'auto',
            'reasoning': 'low',
            'stream_response': True,
            **_ROSTER,
        },
    ]


@pytest.mark.parametrize('verbose', [False, True])
def test_swarm_time_travel_stream_streams_the_replacement_session(*, verbose: bool) -> None:
    """A rewind stream posts the rewind with streaming on, then follows the session."""
    server = _Server()
    swarm = _swarm(server)

    events = list(
        swarm.time_travel_thread_stream(
            'thr-1', checkpoint_id='cp-1', message='redo', model='fast', verbose=verbose
        )
    )

    assert [event.event_type for event in events] == ['final']
    assert server.bodies('/v1/threads/thr-1/time-travel') == [
        {
            'checkpoint_id': 'cp-1',
            'message': {'role': 'user', 'content': 'redo'},
            'run_config': {**_run_config('thr-1'), 'model_directive': 'fast'},
            'model': 'auto',
            'stream_response': True,
            **_ROSTER,
        },
    ]
    assert [path for _method, path, _body in server.calls][-1] == '/v1/sessions/ses-1/events'


def test_swarm_invoke_variants_return_the_final_response() -> None:
    """Streamed, async, batched, and follow-up invocations all reach the final message."""
    server = _Server()
    swarm = _swarm(server)

    streamed = swarm.invoke('one', stream_response=True, thread_id='thr-given')
    awaited = asyncio.run(swarm.ainvoke('two'))
    batched = swarm.batch(['three', 'four'], max_concurrency=1)
    abatched = asyncio.run(swarm.abatch(['five']))
    followed = swarm.allow_followup_questions(input_handler=lambda _q, _s: 'yes').invoke('six')

    responses: list[InvokeResponse] = [streamed, awaited, *batched, *abatched, followed]
    assert [response.response for response in responses] == ['done'] * 6
    assert streamed.thread_id == 'thr-given'
    invoke_bodies = server.bodies('/v1/invoke')
    assert [body['messages'][0]['content'] for body in invoke_bodies] == [
        'one',
        'two',
        'three',
        'four',
        'five',
        'six',
    ]
    assert invoke_bodies[0]['stream_response'] is True
    assert 'stream_response' not in invoke_bodies[1]
    assert invoke_bodies[5]['followup_questions'] == {
        'max_questions_per_run': 3,
        'max_questions_per_thread': 10,
    }


def test_member_builder_records_peer_edges_on_the_roster_wire() -> None:
    """Member dependencies on peers and tools travel on the roster as peer_dependencies."""
    server = _Server()
    swarm = Swarm(name='team', client=server.client())

    def load_context() -> str:
        return 'context'

    researcher = Agent(name='researcher', client=server.client(), use_as_final_output=True)
    writer = Agent(name='writer', client=server.client(), model='fast', system_prompt='Write.')

    assert swarm.member() is not None
    registered = swarm.member(lambda: researcher)
    swarm.member.depends_on_agent(researcher, 'notes').depends_on_agent(
        'editor', 'review'
    ).depends_on_tool(load_context, 'context').depends_on_tool('fetch_sources', 'sources')(writer)
    swarm.add_agent(writer)
    swarm.member.depends_on_agent('editor', 'review')(writer)

    _ = swarm.invoke('go')

    assert registered is researcher
    assert swarm.list_agents() == [researcher, writer]
    body = server.bodies('/v1/invoke')[0]
    assert body['agents'] == [
        {'name': 'researcher', 'model': 'auto', 'use_as_final_output': True},
        {
            'name': 'writer',
            'system_prompt': 'Write.',
            'model': 'fast',
            'peer_dependencies': [
                {'kind': 'agent', 'name': 'researcher', 'arg_name': 'notes'},
                {'kind': 'agent', 'name': 'editor', 'arg_name': 'review'},
                {'kind': 'tool', 'name': 'load_context', 'arg_name': 'context'},
                {'kind': 'tool', 'name': 'fetch_sources', 'arg_name': 'sources'},
            ],
        },
    ]
    assert body['swarm_roster'] is True


def test_member_builder_rejects_non_agent_factories_and_bad_metadata() -> None:
    """A factory must build an Agent, and existing dependency metadata must be a list."""
    swarm = Swarm(name='team')
    agent = Agent(name='member', api_key='test-key', base_url='http://testserver')
    setattr(agent, '__maivn_tool_dependencies__', 'not-a-list')  # noqa: B010 - malformed metadata.

    with pytest.raises(TypeError) as not_agent:
        swarm.member(lambda: 'not an agent')
    with pytest.raises(TypeError) as bad_metadata:
        swarm.member.depends_on_agent('peer', 'notes')(agent)

    assert (
        str(not_agent.value)
        == 'swarm.member expects an Agent instance or zero-argument Agent factory'
    )
    assert str(bad_metadata.value) == '__maivn_tool_dependencies__ metadata must be a list'


def test_swarm_construction_adopts_member_clients() -> None:
    """A swarm takes its first member's client, re-adding a member is a no-op."""
    server = _Server()
    first = Agent(name='first', client=server.client())
    swarm = Swarm(name='team')

    swarm.add_agent(first)
    swarm.add_agent(first)

    assert swarm.client is first.client
    assert swarm.list_agents() == [first]
    assert Swarm(name='team', agents=[first]).client is first.client
    first.client = None
    with pytest.raises(RuntimeError, match=r'^First swarm agent client was not initialized$'):
        _ = Swarm(name='team', agents=[first])


def _final_agent(name: str, *, designated: bool = False) -> Agent:
    return Agent(
        name=name, api_key='test-key', base_url='http://testserver', use_as_final_output=designated
    )


def test_swarm_validation_rejects_conflicting_final_output_declarations() -> None:
    """Swarm-level final tools conflict with designated members; two designations conflict."""
    conflicting = Swarm(name='conflict', agents=[_final_agent('lead', designated=True)])
    _ = conflicting.add_tool(lambda: 'x', name='swarm_final', final_tool=True)
    doubled = Swarm(
        name='double',
        agents=[_final_agent('one', designated=True), _final_agent('two', designated=True)],
    )

    with pytest.raises(ValueError) as conflict:  # noqa: PT011 - exact message asserted below.
        conflicting.validate_tool_configuration()
    with pytest.raises(ValueError) as double:  # noqa: PT011 - exact message asserted below.
        doubled.validate_tool_configuration()
    with pytest.raises(ValueError) as forced_conflict:  # noqa: PT011 - exact message asserted below.
        _ = conflicting.invoke('go', force_final_tool=True)
    with pytest.raises(ValueError) as forced_missing:  # noqa: PT011 - exact message asserted below.
        _ = Swarm(name='empty', agents=[_final_agent('plain')]).invoke('go', force_final_tool=True)

    assert str(conflict.value) == (
        'swarm has conflicting final-output declarations: swarm-scope final_tool and '
        "use_as_final_output agent(s) ['lead']"
    )
    assert str(double.value) == "swarm has multiple use_as_final_output agents: ['one', 'two']"
    assert str(forced_conflict.value) == (
        'Swarm.invoke(force_final_tool=True) has conflicting final-output declarations: the swarm '
        "owns a swarm-scope final_tool and sub-agent(s) ['lead'] are marked "
        'use_as_final_output=True.'
    )
    assert str(forced_missing.value) == (
        'Swarm.invoke(force_final_tool=True) requires at least one of: a swarm-scope tool with '
        'final_tool=True, an agent with use_as_final_output=True, or an agent that owns a tool '
        'with final_tool=True.'
    )


def test_swarm_skill_authoring_scopes_records_to_the_swarm(tmp_path: Path) -> None:
    """Skills and skill sets register under the swarm scope and pin created skills."""
    server = _Server()
    swarm = _swarm(server)
    skill_file = tmp_path / 'skill.md'
    skill_file.write_text(
        '---\nskill: Leaf\ndescription: Leaf skill.\nkind: skill\n---\n## Steps\n1. Act\n',
        encoding='utf-8',
    )
    set_dir = tmp_path / 'set'
    set_dir.mkdir()
    (set_dir / 'SET.md').write_text(
        '---\nskill: Family\ndescription: Family set.\nkind: set\n---\n', encoding='utf-8'
    )

    inline = swarm.add_skill(
        name='Inline', description='Inline skill.', steps=['Look', SkillStep(index=2, action='Act')]
    )
    model = swarm.add_skill(
        Skill(name='Model', description='Model skill.', steps=(SkillStep(index=1, action='Go'),))
    )
    registered = swarm.add_skill_set(set_dir)
    hardcoded = swarm.add_skill_set(SkillSet(name='Coded', description='Coded set.'))

    assert [inline.memory_id, model.memory_id] == ['skill-1', 'skill-2']
    assert swarm.attached_skill_ids == ['skill-1', 'skill-2']
    assert swarm.skill_sets == [registered, hardcoded]
    assert swarm.attach_skill('skill-1') == 'skill-1'
    assert swarm.attached_skill_ids == ['skill-1', 'skill-2']
    assert server.bodies('/v1/skills') == [
        {
            'name': 'Inline',
            'description': 'Inline skill.',
            'steps': [{'index': 1, 'action': 'Look'}, {'index': 2, 'action': 'Act'}],
            'scope': {'shared': True, 'swarm_id': 'team', 'sharing_scope': 'swarm'},
        },
        {
            'name': 'Model',
            'description': 'Model skill.',
            'steps': [{'index': 1, 'action': 'Go'}],
            'scope': {'shared': True, 'swarm_id': 'team', 'sharing_scope': 'swarm'},
        },
    ]
    assert server.bodies('/v1/skill-sets') == [
        {
            'name': 'Family',
            'description': 'Family set.',
            'scope': {'shared': True, 'swarm_id': 'team', 'sharing_scope': 'swarm'},
        },
        {
            'name': 'Coded',
            'description': 'Coded set.',
            'scope': {'shared': True, 'swarm_id': 'team', 'sharing_scope': 'swarm'},
        },
    ]
    with pytest.raises(ValueError, match=r'^add_skill_set path must resolve to a skill set$'):
        _ = swarm.add_skill_set(skill_file)


@pytest.mark.parametrize(
    ('kwargs', 'expected'),
    [
        (
            {
                'skill': Skill(
                    name='S', description='D.', steps=(SkillStep(index=1, action='Go'),)
                ),
                'name': 'X',
            },
            'pass either a Skill or inline skill fields, not both',
        ),
        (
            {'name': 'X', 'description': 'D.'},
            'inline add_skill requires name, description, and steps',
        ),
    ],
)
def test_swarm_add_skill_rejects_ambiguous_or_incomplete_input(
    kwargs: JsonObject, expected: str
) -> None:
    """add_skill takes either a Skill or a complete inline definition."""
    swarm = _swarm(_Server())

    with pytest.raises(ValueError) as raised:  # noqa: PT011 - exact message asserted below.
        _ = swarm.add_skill(**kwargs)

    assert str(raised.value) == expected


def test_swarm_use_skills_sends_auto_retrieval_and_validates_bounds() -> None:
    """Automatic skill retrieval is opt-in, trimmed, and bounded to 1..16."""
    server = _Server()
    swarm = _swarm(server)

    assert swarm.use_skills('  renewals  ', limit=16) is swarm
    _ = swarm.invoke('go')

    body = server.bodies('/v1/invoke')[0]
    assert (body['auto_skills'], body['auto_skills_query'], body['auto_skills_limit']) == (
        True,
        'renewals',
        16,
    )
    for query, limit, expected in (
        ('  ', 1, 'skill retrieval query must not be blank'),
        ('q', 0, 'skill retrieval limit must be at least 1'),
        ('q', 17, 'skill retrieval limit must be at most 16'),
    ):
        with pytest.raises(ValueError) as raised:  # noqa: PT011 - exact message asserted below.
            _ = swarm.use_skills(query, limit=limit)
        assert str(raised.value) == expected
    with pytest.raises(ValueError, match=r'^attached skill id must not be blank$'):
        _ = swarm.attach_skill('   ')


def test_designated_member_satisfies_forced_final_output() -> None:
    """A use_as_final_output member alone is enough for force_final_tool on a swarm."""
    server = _Server()
    lead = Agent(name='lead', client=server.client(), use_as_final_output=True)
    swarm = Swarm(name='team', agents=[lead])

    response = swarm.invoke('go', force_final_tool=True)

    body = server.bodies('/v1/invoke')[0]
    assert response.response == 'done'
    assert body['force_final_tool'] is True
    assert body['agents'] == [{'name': 'lead', 'model': 'auto', 'use_as_final_output': True}]
