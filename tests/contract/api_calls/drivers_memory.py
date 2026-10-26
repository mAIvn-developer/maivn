"""Drive every memory route through the public `Client.memory` facade.

Request models are filled with non-default values so the recorded body carries
every field the SDK can send (the SDK drops defaults and `None` on the wire).
"""

from __future__ import annotations

from typing import Any

from maivn import (
    MemoryInsightCreate,
    MemoryInsightUpdate,
    MemoryScopeRequest,
    MemorySkillCreate,
    MemorySkillStep,
    MemorySkillUpdate,
)
from tests.contract.api_calls.recording import FakeApi, Reply

SCOPE_REQUEST = MemoryScopeRequest(
    project_id='prj_1',
    organization_id='org_1',
    agent_id='agt_1',
    swarm_id='swm_1',
    sharing_scope='swarm',
)
SCOPE = {'project_id': 'prj_1', 'sharing_scope': 'project'}
STEP = MemorySkillStep(
    index=1,
    action='look it up',
    tool='lookup',
    parameters={'key': 'value'},
    expected_output='a value',
)
SKILL: dict[str, Any] = {
    'kind': 'skill',
    'memory_id': 'mem_skill_1',
    'scope': SCOPE,
    'name': 'Lookup',
    'description': 'Look a value up.',
    'steps': [{'index': 1, 'action': 'look it up'}],
    'confidence': 0.9,
    'origin': 'user_defined',
    'status': 'active',
    'created_at': '2026-10-09T00:00:00Z',
    'updated_at': '2026-10-09T00:00:00Z',
}
INSIGHT: dict[str, Any] = {
    'kind': 'insight',
    'memory_id': 'mem_insight_1',
    'scope': SCOPE,
    'insight_type': 'lesson',
    'content': 'Prefer the cache.',
    'relevance_score': 0.7,
    'origin': 'user_promoted',
    'created_at': '2026-10-09T00:00:00Z',
    'updated_at': '2026-10-09T00:00:00Z',
}
DELETED = {'success': True}


async def memory_skills(api: FakeApi) -> None:
    """List, read, create, update and delete skill memory."""
    api.answer('GET', '/v1/memory/skills', Reply(json={'items': [SKILL]}))
    api.answer('POST', '/v1/memory/skills', Reply(status=201, json=SKILL))
    api.answer('GET', '/v1/memory/skills/mem_skill_1', Reply(json=SKILL))
    api.answer('PATCH', '/v1/memory/skills/mem_skill_1', Reply(json=SKILL))
    api.answer('DELETE', '/v1/memory/skills/mem_skill_1', Reply(json=DELETED))
    skills = api.client().memory.skills

    await skills.alist(query='lookup', limit=5)
    await skills.aget('mem_skill_1')
    await skills.acreate(
        MemorySkillCreate(
            memory_id='mem_skill_1',
            scope=SCOPE_REQUEST,
            name='Lookup',
            description='Look a value up.',
            steps=(STEP,),
            preconditions={'ready': True},
            postconditions={'done': True},
            confidence=0.5,
            origin='ai_generated',
            status='deprecated',
            source_session_id='ses_1',
            source_invocation_id='inv_1',
        )
    )
    await skills.aupdate(
        'mem_skill_1',
        MemorySkillUpdate(
            scope=SCOPE_REQUEST,
            name='Lookup',
            description='Look a value up.',
            steps=(STEP,),
            preconditions={'ready': True},
            postconditions={'done': True},
            confidence=0.5,
            status='quarantined',
        ),
    )
    await skills.adelete('mem_skill_1')


async def memory_insights(api: FakeApi) -> None:
    """List, read, create, update, promote and delete insight memory."""
    api.answer('GET', '/v1/memory/insights', Reply(json={'items': [INSIGHT]}))
    api.answer('POST', '/v1/memory/insights', Reply(status=201, json=INSIGHT))
    api.answer('GET', '/v1/memory/insights/mem_insight_1', Reply(json=INSIGHT))
    api.answer('PATCH', '/v1/memory/insights/mem_insight_1', Reply(json=INSIGHT))
    api.answer('POST', '/v1/memory/insights/mem_insight_1/promote', Reply(json=INSIGHT))
    api.answer('DELETE', '/v1/memory/insights/mem_insight_1', Reply(json=DELETED))
    insights = api.client().memory.insights

    await insights.alist(query='cache', limit=5)
    await insights.aget('mem_insight_1')
    await insights.acreate(
        MemoryInsightCreate(
            memory_id='mem_insight_1',
            scope=SCOPE_REQUEST,
            insight_type='warning',
            content='Prefer the cache.',
            relevance_score=0.5,
            origin='ai_generated',
            ttl_days=30,
            half_life_days=7,
            context_signature='sig',
            source_session_id='ses_1',
            source_invocation_id='inv_1',
        )
    )
    await insights.aupdate(
        'mem_insight_1',
        MemoryInsightUpdate(
            scope=SCOPE_REQUEST,
            insight_type='optimization',
            content='Prefer the cache.',
            relevance_score=0.5,
            ttl_days=30,
            half_life_days=7,
            context_signature='sig',
        ),
    )
    await insights.apromote('mem_insight_1', target_scope='org')
    await insights.adelete('mem_insight_1')


async def memory_enrichments(api: FakeApi) -> None:
    """Read a session's memory enrichment receipts."""
    receipt = {
        'session_id': 'ses_1',
        'invocation_id': 'inv_1',
        'status': 'completed',
        'skill_count': 1,
        'insight_count': 0,
        'created_at': '2026-10-09T00:00:00Z',
    }
    api.answer('GET', '/v1/memory/enrichments/ses_1', Reply(json={'items': [receipt]}))

    await api.client().memory.enrichments.alist('ses_1')


DRIVERS = (memory_skills, memory_insights, memory_enrichments)
