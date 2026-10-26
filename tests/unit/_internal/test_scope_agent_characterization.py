"""Characterization tests for the public Agent scope thread and skill surfaces."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import AnyUrl, BaseModel

from maivn import (
    Agent,
    ApprovalDecision,
    Client,
    ClientConfig,
    ProviderUsage,
    Skill,
    SkillStep,
    StreamEvent,
    ThreadAccepted,
)

if TYPE_CHECKING:
    from pathlib import Path

JsonObject = dict[str, Any]
_ACCEPTED: JsonObject = {'thread_id': 'thr-1', 'session_id': 'ses-1', 'stream_position': 0}
_USAGE: JsonObject = {
    'session_id': 'ses-1',
    'scope': 'session',
    'settled': True,
    'as_of': '2026-09-13T12:00:00Z',
    'provider_counts_complete': True,
    'calls': 2,
    'input_tokens': 10,
    'output_tokens': 5,
    'cache_read_input_tokens': 0,
    'cache_creation_input_tokens': 0,
    'by_model': [],
}


class _Summary(BaseModel):
    """A short typed summary."""

    answer: str


def _final_sse(content: str) -> httpx.Response:
    event: JsonObject = {
        'event_id': 'evt-final',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': 'ses-1',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-final',
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

    def __init__(self, *, return_memory_id: bool = True) -> None:
        self.calls: list[tuple[str, str, JsonObject | None]] = []
        self._return_memory_id = return_memory_id

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - one route each.
        body = cast('JsonObject', json.loads(request.content)) if request.content else None
        path = request.url.path
        self.calls.append((request.method, path, body))
        if request.method == 'POST' and path.startswith('/v1/threads'):
            return httpx.Response(HTTPStatus.ACCEPTED, json=_ACCEPTED)
        if path.endswith('/events'):
            return _final_sse('{"answer": "typed"}')
        if path == '/v1/threads/thr-1':
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    'thread_id': 'thr-1',
                    'status': 'idle',
                    'created_at': '2026-01-01T00:00:00Z',
                    'updated_at': '2026-01-01T00:00:00Z',
                },
            )
        if path == '/v1/sessions/ses-1/usage':
            return httpx.Response(HTTPStatus.OK, json=_USAGE)
        if path == '/v1/skills':
            assert body is not None
            record = {**body, 'kind': 'skill'}
            if self._return_memory_id:
                record['memory_id'] = 'skill-1'
            return httpx.Response(HTTPStatus.OK, json=record)
        if path == '/v1/skill-sets':
            assert body is not None
            return httpx.Response(HTTPStatus.OK, json={**body, 'kind': 'skill_set', 'id': 'set-1'})
        return httpx.Response(HTTPStatus.NOT_FOUND, json={'detail': 'unexpected'})

    def client(self) -> Client:
        return Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
            transport=httpx.MockTransport(self.handle),
            private_data_store=None,
        )

    def body(self, path: str) -> JsonObject:
        (body,) = [
            body for _method, called, body in self.calls if called == path and body is not None
        ]
        stable = cast('JsonObject', json.loads(json.dumps(body)))
        messages = cast('list[JsonObject]', stable.get('messages', []))
        if 'message' in stable:
            messages = [*messages, cast('JsonObject', stable['message'])]
        for message in messages:
            _ = message.pop('message_id')
            _ = message.pop('ts')
        return stable


_RUN_CONFIG: JsonObject = {
    'user_id': 'sdk-user',
    'thread_id': 'thr-1',
    'agent_id': 'helper',
    'model_directive': 'auto',
}


def test_agent_thread_requests_carry_agent_identity_and_private_data() -> None:
    """Start, post, and rewind send the agent's run identity and its private data."""
    server = _Server()
    agent = Agent(name='helper', client=server.client(), private_data={'account': 'acct-1'})

    started = agent.start_thread('hi')
    posted = agent.post_thread_message('thr-1', 'more')
    rewound = agent.time_travel_thread('thr-1', checkpoint_id='cp-1', message='redo')

    assert started == posted == rewound == ThreadAccepted(**_ACCEPTED)
    assert server.body('/v1/threads/thr-1/messages') == {
        'message': {'role': 'user', 'content': 'more'},
        'run_config': _RUN_CONFIG,
        'model': 'auto',
        'private_data': {'account': 'acct-1'},
    }
    assert server.body('/v1/threads/thr-1/time-travel') == {
        'checkpoint_id': 'cp-1',
        'message': {'role': 'user', 'content': 'redo'},
        'run_config': _RUN_CONFIG,
        'model': 'auto',
        'private_data': {'account': 'acct-1'},
    }
    started_body = server.body('/v1/threads')
    assert started_body['messages'] == [{'role': 'user', 'content': 'hi'}]
    assert started_body['private_data'] == {'account': 'acct-1'}


def test_agent_async_time_travel_stream_plans_structured_output() -> None:
    """An async rewind stream forces the structured-output tool and posts its schema."""
    server = _Server()
    agent = Agent(name='helper', client=server.client())

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in agent.atime_travel_thread_stream(
                'thr-1',
                checkpoint_id='cp-1',
                message='redo',
                branch_from_message_id='msg-0',
                structured_output=_Summary,
                verbose=True,
            )
        ]

    events = asyncio.run(collect())

    assert [event.event_type for event in events] == ['final']
    body = server.body('/v1/threads/thr-1/time-travel')
    assert [tool['name'] for tool in body.pop('tools')] == ['_Summary']
    assert body == {
        'checkpoint_id': 'cp-1',
        'message': {'role': 'user', 'content': 'redo'},
        'run_config': _RUN_CONFIG,
        'model': 'auto',
        'output_schema': {
            'name': '_Summary',
            'schema': {
                'description': 'A short typed summary.',
                'properties': {'answer': {'title': 'Answer', 'type': 'string'}},
                'required': ['answer'],
                'title': '_Summary',
                'type': 'object',
            },
        },
        'stream_response': True,
        'final_tool_ids': ['_Summary'],
        'force_final_tool': True,
        'branch_from_message_id': 'msg-0',
    }


def test_agent_thread_reads_and_approvals() -> None:
    """Approvals, thread state, thread events, and usage read their exact routes."""
    server = _Server()
    agent = Agent(name='helper', client=server.client())

    approval = agent.submit_approval(
        'thr-1', 'int-1', decision=ApprovalDecision(approved=True, decided_by='dana')
    )
    state = agent.get_thread('thr-1')
    events = list(agent.thread_events('thr-1'))

    async def read_usage() -> ProviderUsage:
        return await agent.asession_usage('ses-1')

    usage = asyncio.run(read_usage())

    assert approval == ThreadAccepted(**_ACCEPTED)
    assert state.status == 'idle'
    assert [event.event_type for event in events] == ['final']
    assert (usage.scope, usage.calls, usage.input_tokens) == ('session', 2, 10)
    assert [(method, path) for method, path, _body in server.calls] == [
        ('POST', '/v1/threads/thr-1/approvals/int-1'),
        ('GET', '/v1/threads/thr-1'),
        ('GET', '/v1/threads/thr-1/events'),
        ('GET', '/v1/sessions/ses-1/usage'),
    ]


def test_agent_skill_set_registration_scopes_to_the_agent(tmp_path: Path) -> None:
    """A markdown skill set registers under the agent scope and is retained on the agent."""
    server = _Server()
    agent = Agent(name='helper', client=server.client())
    set_dir = tmp_path / 'family'
    set_dir.mkdir()
    (set_dir / 'SET.md').write_text(
        '---\nskill: Family\ndescription: Family.\nkind: set\n---\n', encoding='utf-8'
    )

    registered = agent.add_skill_set(set_dir)

    assert agent.skill_sets == [registered]
    assert server.body('/v1/skill-sets') == {
        'name': 'Family',
        'description': 'Family.',
        'scope': {'shared': True, 'agent_id': 'helper', 'sharing_scope': 'agent'},
    }


def test_agent_skill_without_returned_id_is_kept_but_not_pinned() -> None:
    """A stored skill without a memory_id is retained without being attached."""
    server = _Server(return_memory_id=False)
    agent = Agent(name='helper', client=server.client())

    stored = agent.add_skill(
        Skill(name='S', description='D.', steps=(SkillStep(index=1, action='Go'),))
    )

    assert stored.memory_id is None
    assert agent.skills == [stored]
    assert agent.attached_skill_ids == []


def test_agent_without_client_refuses_to_run() -> None:
    """A cleared client is refused with an exact error instead of creating a new one."""
    agent = Agent(name='helper', client=_Server().client())
    agent.client = None

    with pytest.raises(RuntimeError, match=r'^Agent client was not initialized$'):
        _ = agent.get_thread('thr-1')
