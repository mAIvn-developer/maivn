"""Unit tests for SDK memory wire mapping."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Any, TypeAlias, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import (
    Client,
    ClientConfig,
    MemoryInsightCreate,
    MemoryInsightUpdate,
    MemoryScopeRequest,
    MemorySkillCreate,
    MemorySkillStep,
    MemorySkillUpdate,
)

JsonObject: TypeAlias = dict[str, Any]
_SyncHandler: TypeAlias = Callable[[httpx.Request], httpx.Response]
_AsyncHandler: TypeAlias = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


@pytest.mark.parametrize('graph_count', [None, 0, 4])
def test_memory_enrichment_receipts_distinguish_empty_from_finished(
    graph_count: int | None,
) -> None:
    """Polling uses the authenticated session route, preserving terminal counts."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers['authorization'] == 'Bearer test-key'
        items = (
            []
            if len(calls) == 1
            else [
                {
                    'session_id': 'session-sdk',
                    'invocation_id': 'invoke-sdk',
                    'status': 'completed',
                    'skill_count': 2,
                    'insight_count': 1,
                    **({'graph_edge_count': graph_count} if graph_count is not None else {}),
                    'created_at': '2026-09-07T00:00:00Z',
                }
            ]
        )
        return httpx.Response(HTTPStatus.OK, json={'items': items})

    client = _client(handler)
    assert client.memory.enrichments.list('session-sdk') == ()
    (receipt,) = client.memory.enrichments.list('session-sdk')
    assert receipt.status == 'completed'
    assert receipt.skill_count == 2  # noqa: PLR2004 - explicit wire fixture
    assert receipt.insight_count == 1
    assert receipt.graph_edge_count == (graph_count or 0)
    assert calls == ['/v1/memory/enrichments/session-sdk'] * 2


def test_client_memory_skill_methods_map_to_v1_memory_routes() -> None:
    """Memory skill helpers map typed requests to the data-plane route surface."""
    calls: list[tuple[str, str, JsonObject | None, dict[str, str], str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                _json_body(request),
                dict(request.url.params),
                request.headers.get('authorization'),
            ),
        )
        if request.method == 'GET' and request.url.path == '/v1/memory/skills':
            return httpx.Response(HTTPStatus.OK, json={'items': [_skill_json()]})
        if request.method == 'DELETE':
            return httpx.Response(HTTPStatus.OK, json={'success': True})
        description = 'Updated renewal escalation.'
        if request.method == 'PATCH':
            return httpx.Response(HTTPStatus.OK, json=_skill_json(description=description))
        return httpx.Response(HTTPStatus.OK, json=_skill_json())

    client = _client(handler)
    step = MemorySkillStep(
        index=1,
        action='Review blockers',
        tool='lookup_account',
        parameters={'tier': 'enterprise'},
        expected_output='Blocker summary',
    )
    scope = MemoryScopeRequest(project_id='project-sdk', organization_id='org-sdk')

    created = client.memory.skills.create(
        MemorySkillCreate(
            memory_id='skill-sdk',
            scope=scope,
            name='Renewal escalation',
            description='Escalate renewal blockers.',
            steps=(step,),
            preconditions={'stage': 'renewal'},
            postconditions={'owner': 'legal'},
        ),
    )
    listed = client.memory.skills.list(query='renewal', limit=4)
    fetched = client.memory.skills.get('skill-sdk')
    updated = client.memory.skills.update(
        'skill-sdk',
        MemorySkillUpdate(description='Updated renewal escalation.'),
    )
    deleted = client.memory.skills.delete('skill-sdk')

    assert created.memory_id == 'skill-sdk'
    assert created.steps[0].parameters == {'tier': 'enterprise'}
    assert [item.memory_id for item in listed] == ['skill-sdk']
    assert fetched.name == 'Renewal escalation'
    assert updated.description == 'Updated renewal escalation.'
    assert deleted.success is True
    assert calls == [
        ('POST', '/v1/memory/skills', _skill_create_payload(), {}, 'Bearer test-key'),
        ('GET', '/v1/memory/skills', None, {'query': 'renewal', 'limit': '4'}, 'Bearer test-key'),
        ('GET', '/v1/memory/skills/skill-sdk', None, {}, 'Bearer test-key'),
        (
            'PATCH',
            '/v1/memory/skills/skill-sdk',
            {'description': 'Updated renewal escalation.'},
            {},
            'Bearer test-key',
        ),
        ('DELETE', '/v1/memory/skills/skill-sdk', None, {}, 'Bearer test-key'),
    ]


def test_client_memory_insight_methods_map_to_v1_memory_routes() -> None:
    """Memory insight helpers map CRUD and promotion to the data-plane routes."""
    calls: list[tuple[str, str, JsonObject | None, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (request.method, request.url.path, _json_body(request), dict(request.url.params)),
        )
        if request.method == 'GET' and request.url.path == '/v1/memory/insights':
            return httpx.Response(HTTPStatus.OK, json={'items': [_insight_json()]})
        if request.method == 'DELETE':
            return httpx.Response(HTTPStatus.OK, json={'success': True})
        if request.url.path.endswith('/promote'):
            return httpx.Response(
                HTTPStatus.OK,
                json=_insight_json(
                    memory_id='insight-promoted-sdk',
                    origin='user_promoted',
                    ttl_days=None,
                    scope={'sharing_scope': 'project', 'agent_id': None},
                ),
            )
        if request.method == 'PATCH':
            return httpx.Response(
                HTTPStatus.OK,
                json=_insight_json(content='Updated renewal insight.'),
            )
        return httpx.Response(HTTPStatus.OK, json=_insight_json())

    client = _client(handler)
    scope = MemoryScopeRequest(
        project_id='project-sdk',
        organization_id='org-sdk',
        sharing_scope='agent',
        agent_id='agent-sdk',
    )

    created = client.memory.insights.create(
        MemoryInsightCreate(
            memory_id='insight-sdk',
            scope=scope,
            insight_type='warning',
            content='Renewal blockers need legal context.',
            relevance_score=0.9,
            ttl_days=30,
            half_life_days=14,
            context_signature='renewal-context',
        ),
    )
    listed = client.memory.insights.list(query='renewal', limit=6)
    fetched = client.memory.insights.get('insight-sdk')
    updated = client.memory.insights.update(
        'insight-sdk',
        MemoryInsightUpdate(content='Updated renewal insight.'),
    )
    promoted = client.memory.insights.promote('insight-sdk', target_scope='project')
    deleted = client.memory.insights.delete('insight-sdk')

    assert created.memory_id == 'insight-sdk'
    assert [item.memory_id for item in listed] == ['insight-sdk']
    assert fetched.scope.agent_id == 'agent-sdk'
    assert updated.content == 'Updated renewal insight.'
    assert promoted.memory_id == 'insight-promoted-sdk'
    assert promoted.ttl_days is None
    assert deleted.success is True
    assert calls == [
        ('POST', '/v1/memory/insights', _insight_create_payload(), {}),
        ('GET', '/v1/memory/insights', None, {'query': 'renewal', 'limit': '6'}),
        ('GET', '/v1/memory/insights/insight-sdk', None, {}),
        (
            'PATCH',
            '/v1/memory/insights/insight-sdk',
            {'content': 'Updated renewal insight.'},
            {},
        ),
        ('POST', '/v1/memory/insights/insight-sdk/promote', {'target_scope': 'project'}, {}),
        ('DELETE', '/v1/memory/insights/insight-sdk', None, {}),
    ]


def _client(handler: _SyncHandler | _AsyncHandler) -> Client:
    transport = httpx.MockTransport(handler)
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=transport,
    )


def _json_body(request: httpx.Request) -> JsonObject | None:
    if not request.content:
        return None
    return cast('JsonObject', json.loads(request.content.decode('utf-8')))


def _memory_scope_json(
    *,
    sharing_scope: str = 'project',
    agent_id: str | None = None,
    swarm_id: str | None = None,
) -> JsonObject:
    return {
        'project_id': 'project-sdk',
        'organization_id': 'org-sdk',
        'agent_id': agent_id,
        'swarm_id': swarm_id,
        'sharing_scope': sharing_scope,
    }


def _skill_json(*, description: str = 'Escalate renewal blockers.') -> JsonObject:
    return {
        'kind': 'skill',
        'memory_id': 'skill-sdk',
        'scope': _memory_scope_json(),
        'name': 'Renewal escalation',
        'description': description,
        'steps': [
            {
                'index': 1,
                'action': 'Review blockers',
                'tool': 'lookup_account',
                'parameters': {'tier': 'enterprise'},
                'expected_output': 'Blocker summary',
            },
        ],
        'preconditions': {'stage': 'renewal'},
        'postconditions': {'owner': 'legal'},
        'confidence': 1.0,
        'origin': 'user_defined',
        'status': 'active',
        'created_at': '2026-07-05T12:00:00Z',
        'updated_at': '2026-07-05T12:00:00Z',
    }


def _skill_create_payload() -> JsonObject:
    return {
        'memory_id': 'skill-sdk',
        'scope': {'project_id': 'project-sdk', 'organization_id': 'org-sdk'},
        'name': 'Renewal escalation',
        'description': 'Escalate renewal blockers.',
        'steps': [
            {
                'index': 1,
                'action': 'Review blockers',
                'tool': 'lookup_account',
                'parameters': {'tier': 'enterprise'},
                'expected_output': 'Blocker summary',
            },
        ],
        'preconditions': {'stage': 'renewal'},
        'postconditions': {'owner': 'legal'},
    }


def _insight_json(
    *,
    memory_id: str = 'insight-sdk',
    content: str = 'Renewal blockers need legal context.',
    origin: str = 'user_promoted',
    ttl_days: int | None = 30,
    scope: JsonObject | None = None,
) -> JsonObject:
    default_scope: JsonObject = {'sharing_scope': 'agent', 'agent_id': 'agent-sdk'}
    resolved_scope = scope if scope is not None else default_scope
    return {
        'kind': 'insight',
        'memory_id': memory_id,
        'scope': _memory_scope_json(**resolved_scope),
        'insight_type': 'warning',
        'content': content,
        'relevance_score': 0.9,
        'origin': origin,
        'ttl_days': ttl_days,
        'half_life_days': 14,
        'context_signature': 'renewal-context',
        'created_at': '2026-07-05T12:00:00Z',
        'updated_at': '2026-07-05T12:00:00Z',
    }


def _insight_create_payload() -> JsonObject:
    return {
        'memory_id': 'insight-sdk',
        'scope': {
            'project_id': 'project-sdk',
            'organization_id': 'org-sdk',
            'agent_id': 'agent-sdk',
            'sharing_scope': 'agent',
        },
        'insight_type': 'warning',
        'content': 'Renewal blockers need legal context.',
        'relevance_score': 0.9,
        'ttl_days': 30,
        'half_life_days': 14,
        'context_signature': 'renewal-context',
    }
