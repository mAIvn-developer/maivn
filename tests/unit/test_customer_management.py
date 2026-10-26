"""Independent SDK subject facades cannot bleed headers across concurrent calls."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from maivn._internal.memory import MemoryClient
from maivn._internal.resources import ResourcesClient
from maivn._internal.scope.skill_authoring import (
    agent_scope,
    skill_request,
    skill_set_with_scope,
)
from maivn._internal.skills.client import SkillsClient
from maivn._internal.skills.models import Skill, SkillScopeRequest, SkillSet, SkillStep
from maivn._internal.transport.http import HttpJsonClient


def test_subject_facades_are_concurrency_safe() -> None:
    """Subject facades are concurrency safe."""

    async def run() -> None:
        calls: list[tuple[str, str | None]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0)
            calls.append((request.url.path, request.headers.get('X-Maivn-User-Id')))
            return httpx.Response(200, json={'items': []})

        http = HttpJsonClient(
            base_url='http://local',
            api_key='test',
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        memory = MemoryClient(lambda: http)
        skills = SkillsClient(lambda: http)
        resources = ResourcesClient(lambda: http)
        await asyncio.gather(
            memory.for_user('A').insights.alist(),
            memory.for_user('B').skills.alist(),
            memory.for_user('A').enrichments.alist('session'),
            skills.for_user('B').alist(),
            resources.for_user('A').alist(),
            memory.insights.alist(),
        )
        assert calls.count(('/v1/memory/insights', 'A')) == 1
        assert calls.count(('/v1/memory/insights', None)) == 1
        assert ('/v1/memory/skills', 'B') in calls
        assert ('/v1/memory/enrichments/session', 'A') in calls
        assert ('/v1/skills', 'B') in calls
        assert ('/v1/resources', 'A') in calls

    asyncio.run(run())


def test_collection_facades_carry_their_own_subject() -> None:
    """Each collection facade binds its own subject and leaves its parent unbound."""

    async def run() -> None:
        calls: list[tuple[str, str | None]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.url.path, request.headers.get('X-Maivn-User-Id')))
            return httpx.Response(200, json={'items': []})

        http = HttpJsonClient(
            base_url='http://local',
            api_key='test',
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        memory = MemoryClient(lambda: http)
        skill_sets = SkillsClient(lambda: http).skill_sets
        await asyncio.gather(
            memory.skills.for_user('A').alist(),
            memory.insights.for_user('B').alist(),
            memory.enrichments.for_user('A').alist('session'),
            skill_sets.for_user('B').alist(),
            skill_sets.alist(),
        )
        assert sorted(calls, key=str) == sorted(
            [
                ('/v1/memory/skills', 'A'),
                ('/v1/memory/insights', 'B'),
                ('/v1/memory/enrichments/session', 'A'),
                ('/v1/skill-sets', 'B'),
                ('/v1/skill-sets', None),
            ],
            key=str,
        )

    asyncio.run(run())


@pytest.mark.parametrize('bad', ['', ' ', 'sdk-user', ' A', 'x' * 257])
def test_invalid_subject_rejected_before_http(bad: str) -> None:
    """Invalid subject rejected before http."""
    http = HttpJsonClient(base_url='http://local', api_key='test', timeout_seconds=1)
    with pytest.raises(ValueError, match=r'user_id|customer memory'):
        MemoryClient(lambda: http).for_user(bad)


def test_deliberate_skill_authoring_does_not_reshare_private_extraction() -> None:
    """Scope authoring shares new content and refuses existing private/extracted copies."""
    scope = agent_scope('agent')
    assert scope.shared
    base = Skill(
        name='private', description='customer evidence', steps=(SkillStep(index=1, action='read'),)
    )
    for skill in (
        base.model_copy(update={'scope': SkillScopeRequest(owner_user_id='A')}),
        base.model_copy(update={'origin': 'ai_generated'}),
    ):
        with pytest.raises(ValueError, match='new authored skill'):
            skill_request(skill, name=None, description=None, steps=None, scope=scope)


def test_a_customer_owned_skill_set_is_never_reshared() -> None:
    """A private skill set cannot be re-scoped; an authored one carries the new scope down."""
    child = Skill(name='child', description='authored', steps=(SkillStep(index=1, action='read'),))
    authored = SkillSet(name='authored', description='mine', children=(child,))
    scope = agent_scope('agent')
    shared = skill_set_with_scope(authored, scope)
    assert shared.scope == scope
    assert all(item.scope == scope for item in shared.children)
    private = authored.model_copy(update={'scope': SkillScopeRequest(owner_user_id='A')})
    with pytest.raises(ValueError, match='new authored skill set'):
        skill_set_with_scope(private, scope)
