"""Explicit thread requests preserve the full invocation runtime contract."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Client, ClientConfig, RunOptions
from maivn._internal.models import ToolMetadata

if TYPE_CHECKING:
    from collections.abc import Callable


class _Member:
    """Minimal swarm member accepted by the SDK's structural serializer."""

    name = 'researcher'
    description = 'Research the request.'


@pytest.mark.parametrize('operation', ['start', 'post', 'rewind'])
def test_explicit_thread_requests_carry_invocation_runtime(
    operation: Literal['start', 'post', 'rewind'],
) -> None:
    """Every thread dispatch carries tools, custody, members, and skill selection."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = cast('dict[str, Any]', json.loads(request.content))
        bodies.append(body)
        return httpx.Response(
            202,
            json={
                'thread_id': body['run_config']['thread_id'],
                'session_id': 'ses-thread-runtime',
                'stream_position': 0,
            },
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(cast('Callable[[httpx.Request], httpx.Response]', handler)),
        private_data_store=None,
    )

    def deliver() -> str:
        return 'done'

    tool = ToolMetadata(
        name='deliver',
        description='Deliver the result.',
        input_schema={'type': 'object', 'properties': {}},
        target=deliver,
        final_tool=True,
    )
    options = RunOptions(
        thread_id='thr-runtime',
        attached_skill_ids=('skill-research',),
        auto_skills=True,
        system_tools_config={'allowed_system_tools': ['web_search']},
    )
    kwargs: dict[str, Any] = {
        'options': options,
        'tools': (tool,),
        'private_data': {'account_id': 'acct-private'},
        'force_final_tool': True,
        'swarm_members': (_Member(),),
    }

    if operation == 'start':
        client.start_thread('begin', **kwargs)
    elif operation == 'post':
        client.post_thread_message('thr-runtime', 'continue', **kwargs)
    else:
        client.time_travel_thread(
            'thr-runtime',
            checkpoint_id='ses-checkpoint',
            message='rewrite',
            **kwargs,
        )

    assert len(bodies) == 1
    body = bodies[0]
    assert [item['name'] for item in body['tools']] == ['deliver']
    assert body['private_data'] == {'account_id': 'acct-private'}
    assert body['force_final_tool'] is True
    assert body['final_tool_ids'] == ['deliver']
    assert body['agents'] == [{'name': 'researcher', 'description': 'Research the request.'}]
    assert body['swarm_roster'] is True
    assert body['attached_skill_ids'] == ['skill-research']
    assert body['auto_skills'] is True
    assert body['system_tools_config'] == {'allowed_system_tools': ['web_search']}
