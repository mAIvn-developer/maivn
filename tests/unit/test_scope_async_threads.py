"""Async thread operations stay on their owning scope without losing its defaults."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
import pytest

from maivn import Agent, ApprovalDecision, Client, RunOptions, Swarm


async def _run_thread_operation(scope: Agent | Swarm, operation: str, options: RunOptions) -> None:
    """Exercise one asynchronous scope operation."""
    if operation == 'start':
        receipt = await scope.astart_thread('Start here.')
    elif operation == 'post':
        receipt = await scope.apost_thread_message('thr-1', 'Continue.', options=options)
    elif operation == 'rewind':
        receipt = await scope.atime_travel_thread(
            'thr-1',
            checkpoint_id='cp-1',
            message='Try again.',
            branch_from_message_id='msg-1',
            options=options,
        )
    elif operation == 'approval':
        receipt = await scope.asubmit_approval(
            'thr-1',
            'int-1',
            decision=ApprovalDecision(approved=True, decided_by='usr-1'),
        )
    elif operation == 'state':
        assert (await scope.aget_thread('thr-1')).status == 'completed'
        return
    else:
        assert [event.event_type async for event in scope.athread_events('thr-1')] == ['final']
        return
    assert receipt.thread_id == 'thr-1'


@pytest.mark.parametrize('scope_type', [Agent, Swarm])
@pytest.mark.parametrize('operation', ['start', 'post', 'rewind', 'approval', 'state', 'events'])
def test_scope_thread_operations_work_in_an_async_application(
    scope_type: type[Agent | Swarm],
    operation: str,
) -> None:
    """Call the public async API in an active loop and inspect actual HTTP requests."""
    requests: list[httpx.Request] = []

    def lookup(topic: str) -> str:
        """Look up a topic using a local tool."""
        return topic

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith('/events'):
            return httpx.Response(
                200,
                headers={'content-type': 'text/event-stream'},
                content='id: 1\nevent: final\ndata: {}\n\n',
            )
        if request.method == 'GET':
            return httpx.Response(
                200,
                json={
                    'thread_id': 'thr-1',
                    'status': 'completed',
                    'history': [],
                    'created_at': '2026-09-15T12:00:00Z',
                    'updated_at': '2026-09-15T12:00:00Z',
                },
            )
        return httpx.Response(
            202, json={'thread_id': 'thr-1', 'session_id': 'ses-1', 'stream_position': 0}
        )

    scope = scope_type(
        name='desk',
        system_prompt='Keep answers short.',
        model='test-model',
        tools=[lookup],
        attached_skill_ids=['skill-1'],
        system_tools_config={'allow_private_data': True},
        client=Client(
            api_key='test-key',
            base_url='https://data.example',
            transport=httpx.MockTransport(handler),
        ),
    )
    options = RunOptions(model='override-model', user_id='usr-1')

    asyncio.run(_run_thread_operation(scope, operation, options))
    expected_paths = {
        'start': '/v1/threads',
        'post': '/v1/threads/thr-1/messages',
        'rewind': '/v1/threads/thr-1/time-travel',
        'approval': '/v1/threads/thr-1/approvals/int-1',
        'state': '/v1/threads/thr-1',
        'events': '/v1/threads/thr-1/events',
    }
    assert [request.url.path for request in requests] == [expected_paths[operation]]
    if operation in {'start', 'post', 'rewind'}:
        body = cast('dict[str, Any]', json.loads(requests[0].content))
        config = body['run_config']
        assert config['agent_id' if scope_type is Agent else 'swarm_id'] == 'desk'
        assert body['model'] == ('test-model' if operation == 'start' else 'override-model')
        assert body['system_tools_config']['allow_private_data_in_system_tools'] is True
        assert body['attached_skill_ids'] == ['skill-1']
        assert len(body['tools']) == 1
        if operation == 'start':
            assert [message['content'] for message in body['messages']] == [
                'Keep answers short.',
                'Start here.',
            ]
        if operation == 'rewind':
            assert body['checkpoint_id'] == 'cp-1'
            assert body['branch_from_message_id'] == 'msg-1'


@pytest.mark.parametrize('operation', ['start', 'post', 'rewind'])
def test_async_thread_dispatch_validates_required_final_tool_before_requests(
    operation: str,
) -> None:
    """An invalid scope cannot dispatch tools or register resources remotely."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    agent = Agent(
        name='invalid-final-tool',
        force_final_tool=True,
        client=Client(api_key='test-key', transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ValueError, match='final_tool'):
        asyncio.run(_run_thread_operation(agent, operation, RunOptions()))

    assert requests == []
