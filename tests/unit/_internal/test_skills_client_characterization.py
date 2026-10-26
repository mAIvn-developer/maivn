"""Characterization tests for the SDK first-class skill HTTP client."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any, TypeAlias, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Client, ClientConfig, Skill, SkillSet, SkillStep
from maivn._internal.skills import SkillScopeRequest

JsonObject: TypeAlias = dict[str, Any]
Call: TypeAlias = tuple[str, str, str, JsonObject | None]


def _recording_client(
    calls: list[Call],
    responses: dict[tuple[str, str], JsonObject],
) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            cast('JsonObject', json.loads(request.content.decode('utf-8')))
            if request.content
            else None
        )
        calls.append((request.method, request.url.path, request.url.query.decode('ascii'), body))
        return httpx.Response(HTTPStatus.OK, json=responses[request.method, request.url.path])

    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )


def _skill_record(memory_id: str | None, name: str = 'Renewal escalation') -> JsonObject:
    record: JsonObject = {
        'kind': 'skill',
        'name': name,
        'description': 'Escalate renewal blockers.',
        'steps': [{'index': 1, 'action': 'Review blockers'}],
    }
    if memory_id is not None:
        record['memory_id'] = memory_id
    return record


def _set_record(
    set_id: str | None, *, name: str = 'Renewal', members: tuple[str, ...] = ()
) -> JsonObject:
    record: JsonObject = {
        'kind': 'skill_set',
        'name': name,
        'description': 'Skill set.',
        'member_skill_ids': list(members),
    }
    if set_id is not None:
        record['id'] = set_id
    return record


def test_skill_create_serializes_every_non_default_field() -> None:
    """Only non-default fields and a non-empty scope ride on the create body."""
    calls: list[Call] = []
    client = _recording_client(calls, {('POST', '/v1/skills'): _skill_record('skill-full')})

    created = client.skills.create(
        Skill(
            memory_id='skill-full',
            scope=SkillScopeRequest(project_id='proj-1', agent_id='agent-1'),
            name='Full',
            description='Every field.',
            steps=(
                SkillStep(
                    index=1,
                    action='Look up',
                    tool='lookup',
                    parameters={'depth': 2},
                    expected_output='A record',
                ),
            ),
            preconditions={'needs': 'account'},
            postconditions={'emits': 'summary'},
            confidence=0.5,
            origin='ai_generated',
            status='deprecated',
            source_session_id='ses-1',
            source_invocation_id='inv-1',
        ),
    )

    assert created.memory_id == 'skill-full'
    assert calls == [
        (
            'POST',
            '/v1/skills',
            '',
            {
                'name': 'Full',
                'description': 'Every field.',
                'steps': [
                    {
                        'index': 1,
                        'action': 'Look up',
                        'tool': 'lookup',
                        'parameters': {'depth': 2},
                        'expected_output': 'A record',
                    },
                ],
                'memory_id': 'skill-full',
                'scope': {
                    'project_id': 'proj-1',
                    'agent_id': 'agent-1',
                    'sharing_scope': 'project',
                },
                'preconditions': {'needs': 'account'},
                'postconditions': {'emits': 'summary'},
                'confidence': 0.5,
                'origin': 'ai_generated',
                'status': 'deprecated',
                'source_session_id': 'ses-1',
                'source_invocation_id': 'inv-1',
            },
        ),
    ]


@pytest.mark.parametrize(
    ('scope', 'expected'),
    [
        (SkillScopeRequest(), None),
        (SkillScopeRequest(sharing_scope='org'), {'sharing_scope': 'org'}),
        (
            SkillScopeRequest(organization_id='org-1', swarm_id='swarm-1', sharing_scope='swarm'),
            {'organization_id': 'org-1', 'swarm_id': 'swarm-1', 'sharing_scope': 'swarm'},
        ),
    ],
)
def test_skill_set_create_scope_and_parent_serialization(
    scope: SkillScopeRequest,
    expected: JsonObject | None,
) -> None:
    """A default project scope is omitted; anything else is sent with its sharing scope."""
    calls: list[Call] = []
    client = _recording_client(calls, {('POST', '/v1/skill-sets'): _set_record('set-1')})

    created = client.skills.skill_sets.create(
        SkillSet(
            id='set-1', scope=scope, name='Renewal', description='Desc.', parent_set_id='parent-1'
        ),
    )

    body: JsonObject = {'name': 'Renewal', 'description': 'Desc.', 'id': 'set-1'}
    if expected is not None:
        body['scope'] = expected
    body['parent_set_id'] = 'parent-1'
    assert created.id == 'set-1'
    assert calls == [('POST', '/v1/skill-sets', '', body)]
    assert list(cast('JsonObject', calls[0][3])) == list(body)


def test_skill_reads_and_set_queries_use_their_routes() -> None:
    """Get, list, and scoped search map onto the documented skill routes and params."""
    calls: list[Call] = []
    client = _recording_client(
        calls,
        {
            ('GET', '/v1/skills/skill-1'): _skill_record('skill-1'),
            ('GET', '/v1/skills'): {'items': []},
            ('GET', '/v1/skill-sets'): {'items': [_set_record('set-1')]},
            ('GET', '/v1/skill-sets/set-1/index'): {
                'skill_set': _set_record('set-1', members=('skill-1',)),
                'descendant_skill_sets': [],
                'skills': [],
            },
            ('GET', '/v1/skill-sets/set-1/skills'): {'items': [_skill_record('skill-1')]},
            ('POST', '/v1/skill-sets/set-1/skills/skill-1'): _set_record(
                'set-1', members=('skill-1',)
            ),
            ('POST', '/v1/skill-sets/set-1/skill-sets/set-2'): _set_record('set-1'),
        },
    )

    fetched = client.skills.get('skill-1')
    listed_skills = client.skills.list()
    listed_sets = client.skills.skill_sets.list(query='renewal')
    fetched_set = client.skills.skill_sets.get('set-1')
    searched = client.skills.skill_sets.search_skills('set-1', query='blockers', limit=3)
    added = client.skills.skill_sets.add_skill('set-1', 'skill-1')
    nested = client.skills.skill_sets.add_child_set('set-1', 'set-2')

    assert fetched.memory_id == 'skill-1'
    assert listed_skills == ()
    assert [item.id for item in listed_sets] == ['set-1']
    assert fetched_set.member_skill_ids == ('skill-1',)
    assert [child.name for child in fetched_set.children] == [fetched.name]
    assert [item.memory_id for item in searched] == ['skill-1']
    assert added.member_skill_ids == ('skill-1',)
    assert nested.id == 'set-1'
    assert calls == [
        ('GET', '/v1/skills/skill-1', '', None),
        ('GET', '/v1/skills', 'limit=16', None),
        ('GET', '/v1/skill-sets', 'limit=16&query=renewal', None),
        ('GET', '/v1/skill-sets/set-1/index', '', None),
        ('GET', '/v1/skills/skill-1', '', None),
        ('GET', '/v1/skill-sets/set-1/skills', 'limit=3&query=blockers', None),
        ('POST', '/v1/skill-sets/set-1/skills/skill-1', '', {}),
        ('POST', '/v1/skill-sets/set-1/skill-sets/set-2', '', {}),
    ]


def test_async_skill_methods_return_the_same_records() -> None:
    """The async methods are usable directly inside an event loop."""
    calls: list[Call] = []
    client = _recording_client(
        calls,
        {
            ('GET', '/v1/skills/skill-1'): _skill_record('skill-1'),
            ('GET', '/v1/skill-sets'): {'items': []},
        },
    )

    async def run() -> tuple[Skill, tuple[SkillSet, ...]]:
        return await client.skills.aget('skill-1'), await client.skills.skill_sets.alist(limit=2)

    skill, sets = asyncio.run(run())

    assert skill.memory_id == 'skill-1'
    assert sets == ()
    assert calls == [
        ('GET', '/v1/skills/skill-1', '', None),
        ('GET', '/v1/skill-sets', 'limit=2', None),
    ]


def test_registered_tree_returns_linked_sets_with_their_registered_children() -> None:
    """Nested registration returns the linked child set carrying its own registered skills."""
    calls: list[Call] = []
    responses: dict[tuple[str, str], JsonObject] = {
        ('POST', '/v1/skills'): _skill_record('skill-leaf', name='Leaf'),
        ('POST', '/v1/skill-sets/set-child/skills/skill-leaf'): _set_record(
            'set-child', name='Child', members=('skill-leaf',)
        ),
        ('POST', '/v1/skill-sets/set-root/skill-sets/set-child'): _set_record(
            'set-child', name='Linked'
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = cast('JsonObject', json.loads(request.content.decode('utf-8')))
        calls.append((request.method, request.url.path, '', body))
        if request.url.path == '/v1/skill-sets':
            return httpx.Response(
                HTTPStatus.OK, json=_set_record(str(body['id']), name=str(body['name']))
            )
        return httpx.Response(HTTPStatus.OK, json=responses[request.method, request.url.path])

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    leaf = Skill(name='Leaf', description='Leaf skill.', steps=(SkillStep(index=1, action='Act'),))
    tree = SkillSet(
        id='set-root',
        name='Root',
        description='Root set.',
        children=(
            SkillSet(id='set-child', name='Child', description='Child set.', children=(leaf,)),
        ),
    )

    registered = client.skills.register_skill_set_tree(tree)

    assert registered.id == 'set-root'
    assert registered.name == 'Root'
    (linked,) = registered.children
    assert isinstance(linked, SkillSet)
    assert linked.name == 'Linked'
    assert [child.memory_id for child in linked.children if isinstance(child, Skill)] == [
        'skill-leaf'
    ]
    assert [call[1] for call in calls] == [
        '/v1/skill-sets',
        '/v1/skill-sets',
        '/v1/skills',
        '/v1/skill-sets/set-child/skills/skill-leaf',
        '/v1/skill-sets/set-root/skill-sets/set-child',
    ]


def test_tree_registration_requires_created_set_id() -> None:
    """A created set without an id stops registration with a RuntimeError."""
    client = _recording_client([], {('POST', '/v1/skill-sets'): _set_record(None)})

    with pytest.raises(RuntimeError) as raised:
        _ = client.skills.register_skill_set_tree(SkillSet(name='Root', description='Root.'))

    assert str(raised.value) == 'created skill-set response did not include id'


def test_tree_registration_requires_created_skill_memory_id() -> None:
    """A created skill without a memory_id stops registration with a RuntimeError."""
    client = _recording_client(
        [],
        {
            ('POST', '/v1/skill-sets'): _set_record('set-root'),
            ('POST', '/v1/skills'): _skill_record(None),
        },
    )
    tree = SkillSet(
        name='Root',
        description='Root.',
        children=(
            Skill(name='Leaf', description='Leaf.', steps=(SkillStep(index=1, action='Act'),)),
        ),
    )

    with pytest.raises(RuntimeError) as raised:
        _ = client.skills.register_skill_set_tree(tree)

    assert str(raised.value) == 'created skill response did not include memory_id'
