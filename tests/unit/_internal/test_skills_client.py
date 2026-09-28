"""Unit tests for SDK first-class skill HTTP mapping."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Any, TypeAlias, cast

import httpx
from pydantic import AnyUrl

from maivn import Client, ClientConfig, Skill, SkillSet, SkillStep

JsonObject: TypeAlias = dict[str, Any]
_SyncHandler: TypeAlias = Callable[[httpx.Request], httpx.Response]
_AsyncHandler: TypeAlias = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def test_skills_client_creates_lists_and_searches_first_class_skills() -> None:
    """The SDK maps skill model requests to the first-class /v1/skills routes."""
    calls: list[tuple[str, str, JsonObject | None, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                _json_body(request),
                request.url.query.decode('ascii'),
            ),
        )
        if request.method == 'POST':
            return httpx.Response(HTTPStatus.OK, json=_skill_response('skill-renewal'))
        return httpx.Response(HTTPStatus.OK, json={'items': [_skill_response('skill-renewal')]})

    client = _client(handler)

    created = client.skills.create(
        Skill(
            name='Renewal escalation',
            description='Escalate renewal blockers.',
            steps=(SkillStep(index=1, action='Review blockers'),),
        ),
    )
    listed = client.skills.list(query='renewal', limit=4)

    assert created.memory_id == 'skill-renewal'
    assert listed[0].name == 'Renewal escalation'
    assert calls[0] == (
        'POST',
        '/v1/skills',
        {
            'name': 'Renewal escalation',
            'description': 'Escalate renewal blockers.',
            'steps': [{'index': 1, 'action': 'Review blockers'}],
        },
        '',
    )
    assert calls[1] == ('GET', '/v1/skills', None, 'limit=4&query=renewal')


def test_skills_client_registers_skill_set_trees() -> None:
    """Skill-set registration posts sets, skills, and parent-child membership."""
    calls: list[tuple[str, str, JsonObject | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, _json_body(request)))
        body = _json_body(request) or {}
        if request.url.path == '/v1/skill-sets':
            return httpx.Response(
                HTTPStatus.OK,
                json=_skill_set_response(str(body.get('id') or 'set-root')),
            )
        if request.url.path == '/v1/skills':
            return httpx.Response(
                HTTPStatus.OK,
                json=_skill_response(str(body.get('memory_id') or 'skill-created')),
            )
        if '/skills/' in request.url.path:
            return httpx.Response(
                HTTPStatus.OK,
                json=_skill_set_response(request.url.path.split('/')[3], member_ids=('skill-1',)),
            )
        return httpx.Response(
            HTTPStatus.OK,
            json=_skill_set_response(request.url.path.split('/')[3]),
        )

    client = _client(handler)
    tree = SkillSet(
        id='set-root',
        name='Renewal Handling',
        description='Renewal procedures.',
        children=(
            Skill(
                memory_id='skill-1',
                name='Gather Context',
                description='Gather facts.',
                steps=(SkillStep(index=1, action='Collect context'),),
            ),
            SkillSet(
                id='set-child',
                name='Legal Escalation',
                description='Legal procedures.',
                children=(),
            ),
        ),
    )

    registered = client.skills.register_skill_set_tree(tree)

    assert registered.id == 'set-root'
    assert [call[0:2] for call in calls] == [
        ('POST', '/v1/skill-sets'),
        ('POST', '/v1/skills'),
        ('POST', '/v1/skill-sets/set-root/skills/skill-1'),
        ('POST', '/v1/skill-sets'),
        ('POST', '/v1/skill-sets/set-root/skill-sets/set-child'),
    ]


def _client(handler: _SyncHandler | _AsyncHandler) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )


def _json_body(request: httpx.Request) -> JsonObject | None:
    if not request.content:
        return None
    return cast('JsonObject', json.loads(request.content.decode('utf-8')))


def _scope() -> JsonObject:
    return {
        'project_id': 'project-sdk',
        'organization_id': 'org-sdk',
        'sharing_scope': 'project',
    }


def _skill_response(skill_id: str) -> JsonObject:
    return {
        'kind': 'skill',
        'memory_id': skill_id,
        'scope': _scope(),
        'name': 'Renewal escalation',
        'description': 'Escalate renewal blockers.',
        'steps': [{'index': 1, 'action': 'Review blockers'}],
        'preconditions': {},
        'postconditions': {},
        'confidence': 1.0,
        'origin': 'user_defined',
        'status': 'active',
        'created_at': '2026-07-05T12:00:00Z',
        'updated_at': '2026-07-05T12:00:00Z',
    }


def _skill_set_response(set_id: str, *, member_ids: tuple[str, ...] = ()) -> JsonObject:
    return {
        'kind': 'skill_set',
        'id': set_id,
        'scope': _scope(),
        'name': set_id,
        'description': 'Skill set.',
        'member_skill_ids': list(member_ids),
    }
