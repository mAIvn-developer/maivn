"""Unit tests for Agent and Swarm authoring facade behavior."""

from __future__ import annotations

import asyncio
import base64
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from maivn_contracts.runtime import RunConfig
from pydantic import AnyUrl, BaseModel, Field

from maivn import (
    Agent,
    AgentGenerated,
    Client,
    ClientConfig,
    FollowupQuestion,
    MCPServer,
    MemoryConfig,
    ModelChoice,
    ModelConfig,
    PermissionFlag,
    PermissionSet,
    PlanningChoice,
    PrivateData,
    ResourceRegistrationError,
    RunOptions,
    SessionOrchestrationConfig,
    Skill,
    SkillSet,
    SkillStep,
    Swarm,
    SystemToolsConfig,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
    tool_output,
    toolify,
    toolset,
)
from maivn._internal.compat.decorators import (
    EXECUTION_CONTROLS_ATTR,
    PRIVATE_DATA_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
)
from maivn._internal.wire import _agent_spec  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from pathlib import Path

    from maivn._internal.compat.decorators import (
        AgentDependency,
        ExecutionControl,
        PrivateDataDependency,
        ToolDependency,
    )


_AUTO_SKILLS_LIMIT = 3
_PARALLEL_TOOL_COUNT = 2


class _StructuredSummary(BaseModel):
    answer: str


def test_agent_resources_register_once_and_bind_before_invocation() -> None:
    """Declared text resources are uploaded and bound once before the first invoke."""
    resource_posts: list[dict[str, object]] = []
    bind_posts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if request.url.path == '/v1/resources':
            assert body is not None
            resource_posts.append(body)
            return httpx.Response(HTTPStatus.OK, json=_resource_response())
        if request.url.path == '/v1/resources/resource-sdk/bind':
            assert body is not None
            bind_posts.append(body)
            return httpx.Response(
                HTTPStatus.OK,
                json=_resource_response(
                    binding_type='agent',
                    sharing_scope='agent',
                    agent_id='rollout-agent',
                ),
            )
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-resources', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-resources', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    content = 'Owner: Dana Cole. Escalation contact: Nora Kim. Rollback threshold: 2% error rate.'
    agent = Agent(
        name='rollout-agent',
        client=client,
        resources=[
            {
                'name': 'bound-rollout-runbook.txt',
                'mime_type': 'text/plain',
                'text_content': content,
                'binding_type': 'agent',
                'sharing_scope': 'agent',
                'tags': ['rollout', 'runbook'],
            }
        ],
    )

    _ = agent.invoke('recall the rollout details')
    _ = agent.invoke('recall the escalation contact')

    assert len(resource_posts) == 1
    assert resource_posts[0]['name'] == 'bound-rollout-runbook.txt'
    assert resource_posts[0]['media_type'] == 'text/plain'
    assert resource_posts[0]['content_base64'] == base64.b64encode(
        content.encode('utf-8'),
    ).decode('ascii')
    assert resource_posts[0]['tags'] == ['rollout', 'runbook']
    assert bind_posts == [{'binding_type': 'agent', 'target_id': 'rollout-agent'}]


def test_agent_resource_without_text_content_raises_typed_error() -> None:
    """A declared resource cannot be silently dropped when its content is missing."""
    agent = Agent(
        name='invalid-resource-agent',
        api_key='test-key',
        base_url='http://testserver',
        resources=[{'name': 'missing-content.txt', 'mime_type': 'text/plain'}],
    )

    with pytest.raises(ResourceRegistrationError, match='text_content'):
        agent.invoke('hello')


def test_agent_file_resource_uploads_exact_bytes(tmp_path: Path) -> None:
    """The v1-compatible file declaration remains a real resource in v2."""
    source = tmp_path / 'brief.pdf'
    source.write_bytes(b'%PDF-1.7\nexact fixture\n%%EOF')
    resource_posts: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if request.url.path == '/v1/resources':
            assert body is not None
            resource_posts.append(body)
            return httpx.Response(HTTPStatus.OK, json=_resource_response())
        if request.url.path == '/v1/resources/resource-sdk/bind':
            return httpx.Response(HTTPStatus.OK, json=_resource_response())
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-file-resource', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-file-resource', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(
        name='file-resource-agent',
        client=client,
        resources=[
            {
                'name': source.name,
                'mime_type': 'application/pdf',
                'file': source,
                'binding_type': 'agent',
            }
        ],
    )

    _ = agent.invoke('read the brief')

    assert resource_posts[0]['content_base64'] == base64.b64encode(source.read_bytes()).decode(
        'ascii'
    )


def test_swarm_registers_member_resources_before_invocation() -> None:
    """Delegated members must be able to retrieve their own declared resources."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == '/v1/resources':
            return httpx.Response(HTTPStatus.OK, json=_resource_response())
        if request.url.path == '/v1/resources/resource-sdk/bind':
            return httpx.Response(
                HTTPStatus.OK,
                json=_resource_response(
                    binding_type='agent',
                    sharing_scope='agent',
                    agent_id='member-analyst',
                ),
            )
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-member-resource', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-member-resource', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    member = Agent(
        name='member-analyst',
        client=client,
        resources=[
            {
                'name': 'member-brief.txt',
                'mime_type': 'text/plain',
                'text_content': 'Grounded member evidence.',
                'binding_type': 'agent',
            }
        ],
    )

    _ = Swarm(name='resource-swarm', agents=[member]).invoke('synthesize')

    assert paths[:3] == [
        '/v1/resources',
        '/v1/resources/resource-sdk/bind',
        '/v1/invoke',
    ]


def test_agent_without_resources_makes_no_resource_requests() -> None:
    """Agents without declared resources retain their existing invoke wire behavior."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-no-resources', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-no-resources', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )

    _ = Agent(name='no-resources-agent', client=client).invoke('hello')

    assert paths == ['/v1/invoke', '/v1/sessions/ses-no-resources/events']


def test_agent_and_swarm_share_the_v2_http_client_surface() -> None:
    """Agent/Swarm invoke methods delegate to the owned SDK client."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-swarm', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-swarm', 'swarm done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='researcher', system_prompt='Research clearly.', client=client)
    swarm = Swarm(name='team', agents=[agent], client=client)

    agent_response = agent.invoke(
        'hello',
        options=RunOptions(thread_id='thr-agent', user_id='usr-sdk'),
    )
    swarm_response = swarm.invoke(
        'hello',
        options=RunOptions(thread_id='thr-swarm', user_id='usr-sdk'),
    )

    assert agent_response.response == 'swarm done'
    assert swarm_response.response == 'swarm done'
    assert swarm.agents == [agent]


def test_agent_invoke_accepts_verbose_and_force_final_tool_kwargs() -> None:
    """Agent.invoke accepts v1 invocation flags while preserving the v2 wire contract."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-agent-flags', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-agent-flags', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='flag-agent', client=client)
    _ = agent.add_tool(lambda: {'answer': 'ok'}, name='final_answer', final_tool=True)

    response = agent.invoke(
        'hello',
        force_final_tool=True,
        verbose=True,
        options=RunOptions(thread_id='thr-fixed'),
    )

    assert response.response == 'done'
    first_call = calls[0]
    assert first_call is not None
    assert first_call['run_config'] == {
        'user_id': 'sdk-user',
        'thread_id': 'thr-fixed',
        'agent_id': 'flag-agent',
    }


def test_agent_invoke_stamps_origin_on_run_config() -> None:
    """A caller-supplied ``origin`` rides the wire RunConfig, display/filter only."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-agent-origin', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-agent-origin', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='origin-agent', client=client)

    _ = agent.invoke('hello', options=RunOptions(thread_id='thr-origin'), origin='studio')

    first_call = calls[0]
    assert first_call is not None
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert RunConfig.model_validate(run_config) is not None
    assert run_config['origin'] == 'studio'


def test_agent_invoke_omits_origin_when_not_supplied() -> None:
    """Historical/default calls never gain an origin field on the wire."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-agent-no-origin', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-agent-no-origin', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='no-origin-agent', client=client)

    _ = agent.invoke('hello', options=RunOptions(thread_id='thr-no-origin'))

    first_call = calls[0]
    assert first_call is not None
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert 'origin' not in run_config


def test_swarm_invoke_stamps_origin_on_run_config() -> None:
    """Swarm.invoke threads ``origin`` through the same RunConfig path as Agent."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-swarm-origin', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-swarm-origin', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    swarm = Swarm(name='origin-swarm', client=client)

    _ = swarm.invoke('hello', options=RunOptions(thread_id='thr-swarm-origin'), origin='studio')

    first_call = calls[0]
    assert first_call is not None
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert run_config['origin'] == 'studio'


def test_agent_invoke_serializes_local_tools_without_callable_targets() -> None:
    """Invocation-local tool contracts cross HTTP while callable targets stay in process."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-tool-wire', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-tool-wire', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    agent = Agent(
        name='tool-wire-agent',
        client=client,
        private_data={'account_id': 'acct-123'},
    )

    @agent.toolify(
        name='final_answer',
        description='Return the final account answer.',
        final_tool=True,
    )
    @depends_on_private_data(data_key='account_id', arg_name='account_id')
    def final_answer(answer: str, account_id: str) -> dict[str, str]:
        return {'answer': answer, 'account_id': account_id}

    _ = final_answer
    _ = agent.invoke('hello', force_final_tool=True)

    body = calls[0]
    assert body is not None
    assert body['tools'] == [
        {
            'tool_id': 'final_answer',
            'namespace': 'sdk',
            'version': 'v1',
            'idempotency': 'unknown',
            'timeout_ms': 600000,
            'kind': 'function',
            'name': 'final_answer',
            'description': 'Return the final account answer.',
            'input_schema': {
                'type': 'object',
                'properties': {'answer': {'type': 'string'}},
                'additionalProperties': False,
                'required': ['answer'],
            },
        }
    ]
    assert body['private_data'] == {'account_id': 'acct-123'}
    assert body['force_final_tool'] is True
    assert body['final_tool_ids'] == ['final_answer']
    assert 'target' not in json.dumps(body)


def test_agent_invoke_executes_sse_tool_request_and_posts_result() -> None:
    """The SDK executes its local callable from SSE and resumes the same session."""
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    executed: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        calls.append((request.method, request.url.path, body))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-tool-execution', 'stream_position': 0},
            )
        if request.url.path.endswith('/events'):
            return _tool_request_sse_response()
        return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    agent = Agent(
        name='tool-execution-agent',
        client=client,
        private_data={'account_id': 'acct-123'},
    )

    @agent.toolify(name='lookup_account', description='Lookup one account.')
    @depends_on_private_data(data_key='account_id', arg_name='account_id')
    def lookup_account(question: str, account_id: str) -> dict[str, str]:
        executed.append((question, account_id))
        return {'answer': 'active', 'account_id': account_id}

    _ = lookup_account
    response = agent.invoke('hello')

    assert response.response == 'Account is active.'
    assert executed == [('status?', 'acct-123')]
    result_call = next(call for call in calls if '/tools/results' in call[1])
    assert result_call[0] == 'POST'
    assert result_call[1] == '/v1/sessions/ses-tool-execution/tools/results'
    assert result_call[2] is not None
    outcomes = cast('list[dict[str, object]]', result_call[2]['outcomes'])
    outcome = outcomes[0]
    assert outcome['call_id'] == 'call-sdk-1'
    assert outcome['status'] == 'ok'
    assert outcome['result'] == {'answer': 'active', 'account_id': 'acct-123'}


def test_agent_astream_executes_independent_sse_tool_batch_concurrently() -> None:
    """Independent SDK tools overlap instead of serializing the server's parallel batch."""
    active = 0
    max_active = 0
    both_started = asyncio.Event()
    posted_results: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-parallel-tools', 'stream_position': 0},
            )
        if request.url.path.endswith('/events'):
            return _parallel_tool_request_sse_response()
        posted_results.append(request.url.path)
        return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='parallel-tool-agent', client=client)

    async def run_tool(name: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == _PARALLEL_TOOL_COUNT:
            both_started.set()
        try:
            await asyncio.wait_for(both_started.wait(), timeout=0.1)
            return name
        finally:
            active -= 1

    @agent.toolify(name='first_tool')
    async def first_tool() -> str:
        return await run_tool('first')

    @agent.toolify(name='second_tool')
    async def second_tool() -> str:
        return await run_tool('second')

    _ = first_tool, second_tool

    async def consume() -> None:
        _ = [event async for event in agent.astream('run both')]

    asyncio.run(consume())

    assert max_active == _PARALLEL_TOOL_COUNT
    assert posted_results == ['/v1/sessions/ses-parallel-tools/tools/results']


# One test walking a full dependency-tool run end to end; splitting it would
# lose the ordering the assertions depend on.
def test_agent_astream_reports_dependency_tools_when_they_actually_run() -> None:  # noqa: C901
    """Tool lifecycle events follow dependency execution, not server planning order."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-parallel-tools', 'stream_position': 0},
            )
        if request.url.path.endswith('/events'):
            return _parallel_tool_request_sse_response()
        return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    agent = Agent(
        name='dependent-tool-lifecycle-agent',
        client=client,
        private_data={'serial_number': 'SN-SECRET'},
    )

    @depends_on_private_data(data_key='serial_number', arg_name='serial_number')
    @agent.toolify(name='first_tool')
    async def first_tool(serial_number: str) -> str:
        await asyncio.sleep(0.01)
        return f'first:{serial_number}'

    @agent.toolify(name='second_tool')
    @depends_on_tool(first_tool, arg_name='first_result')
    async def second_tool(first_result: str) -> str:
        return f'{first_result}-second'

    _ = second_tool

    async def consume() -> tuple[list[str], dict[str, list[str]]]:
        lifecycle: list[str] = []
        private_data_keys: dict[str, list[str]] = {}
        async for event in agent.astream('run in dependency order'):
            if event.event_type not in {'system_tool_start', 'system_tool_complete'}:
                continue
            payload = event.payload
            tool_name = payload.get('tool_name')
            if isinstance(tool_name, str):
                lifecycle.append(f'{event.event_type}:{tool_name}')
            raw_keys = payload.get('private_data_keys')
            if isinstance(raw_keys, list) and isinstance(tool_name, str):
                private_data_keys[tool_name] = [str(key) for key in cast('list[object]', raw_keys)]
        return lifecycle, private_data_keys

    lifecycle, private_data_keys = asyncio.run(consume())

    assert lifecycle == [
        'system_tool_start:first_tool',
        'system_tool_complete:first_tool',
        'system_tool_start:second_tool',
        'system_tool_complete:second_tool',
    ]
    # The second tool consumes a private dependency result and retains its custody.
    assert private_data_keys == {
        'first_tool': ['serial_number'],
        'second_tool': ['serial_number'],
    }


def test_agent_astream_posts_a_tool_layer_before_a_slow_consumer_renders_it() -> None:
    """UI handling of many start events must not backpressure the layer result post."""
    results_posted = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-parallel-tools', 'stream_position': 0},
            )
        if request.url.path.endswith('/events'):
            return _parallel_tool_request_sse_response()
        results_posted.set()
        return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='nonblocking-tool-events-agent', client=client)

    @agent.toolify(name='first_tool')
    def first_tool() -> str:
        return 'first'

    @agent.toolify(name='second_tool')
    def second_tool() -> str:
        return 'second'

    _ = first_tool, second_tool

    async def consume() -> None:
        async for event in agent.astream('run both'):
            if event.event_type == 'system_tool_start':
                await asyncio.wait_for(results_posted.wait(), timeout=0.1)
                return

    asyncio.run(consume())


def test_agent_invoke_accepts_per_call_runtime_options() -> None:
    """Per-call v1 runtime kwargs are accepted without leaking unsupported wire fields."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-runtime-options', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-runtime-options', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='runtime-agent', client=client)

    response = agent.invoke(
        'hello',
        model='fast',
        memory_config={'enabled': False},
        system_tools_config={'allowed_tools': []},
        orchestration_config={'allow_reevaluate_loop': True},
        options=RunOptions(thread_id='thr-runtime'),
    )

    first_call = calls[0]
    assert first_call is not None
    assert first_call['run_config'] == {
        'user_id': 'sdk-user',
        'thread_id': 'thr-runtime',
        'agent_id': 'runtime-agent',
        'model_directive': 'fast',
    }
    assert first_call['system_tools_config'] == {'allowed_system_tools': []}
    assert first_call['memory'] == {
        'enabled': False,
        'level': 'clarity',
        'persistence_mode': 'vector_plus_graph',
        'retrieval': {
            'skills_enabled': True,
            'insights_enabled': True,
            'resources_enabled': True,
            'max_skills': 3,
            'max_insights': 3,
            'max_resources': 3,
            'max_context_chars': 12000,
        },
        'skill_extraction': {'enabled': True, 'max_count': 2},
        'insight_extraction': {'enabled': True, 'max_count': 2},
    }
    assert 'orchestration_config' not in first_call
    assert response.thread_id == 'thr-runtime'


def test_followup_builder_is_inline_and_preserves_the_invoke_keyword_surface() -> None:
    """Opting in adds config without dropping any existing invocation capability."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-followup-options', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-followup-options', '{"answer":"typed"}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='followup-agent', client=client)

    @agent.toolify(name='lookup')
    def lookup(query: str) -> str:
        return query

    response = agent.allow_followup_questions(
        input_handler=lambda _question, _schema: 'US East',
        max_questions=3,
        max_questions_per_thread=9,
        response_schema={'type': 'string'},
        timeout=45,
    ).invoke(
        'hello',
        targeted_tools=['lookup'],
        structured_output=_StructuredSummary,
        reasoning='high',
        model='fast',
        memory_config={'enabled': False},
        system_tools_config={'allowed_tools': []},
        orchestration_config={'allow_reevaluate_loop': True},
        options=RunOptions(thread_id='thr-followup'),
        origin='studio',
    )

    first_call = calls[0]
    assert first_call is not None
    assert first_call['followup_questions'] == {
        'max_questions_per_run': 3,
        'max_questions_per_thread': 9,
        'response_schema': {'type': 'string'},
        'timeout_seconds': 45,
    }
    assert first_call['run_config'] == {
        'user_id': 'sdk-user',
        'thread_id': 'thr-followup',
        'agent_id': 'followup-agent',
        'model_directive': 'fast',
        'origin': 'studio',
    }
    assert first_call['tools']
    assert first_call['output_schema']
    assert first_call['memory']
    assert first_call['system_tools_config'] == {'allowed_system_tools': []}
    assert isinstance(response.result, _StructuredSummary)
    assert response.result.answer == 'typed'
    assert lookup('kept') == 'kept'


@pytest.mark.parametrize(
    'question_text',
    ['Which region should I use?', 'Q' * 513, 'Q' * 2048],
    ids=['short', 'above-old-limit', 'runtime-limit'],
)
def test_followup_builder_handles_interrupt_and_resumes_same_thread(question_text: str) -> None:
    """The inline handler answers the canonical checkpoint before streaming resumes."""
    handled: list[tuple[FollowupQuestion, dict[str, object]]] = []
    response_bodies: list[dict[str, object]] = []

    def input_handler(
        question: FollowupQuestion,
        schema: dict[str, object],
    ) -> str:
        handled.append((question, schema))
        return 'US East'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-followup-resume', 'stream_position': 0},
            )
        if request.url.path == '/v1/sessions/ses-followup-resume/events':
            interrupt = {
                'kind': 'agent_followup',
                'checkpoint_id': 'int-followup-1',
                'thread_id': 'thr-followup-resume',
                'session_id': 'ses-followup-resume',
                'header': 'Region',
                'question': question_text,
                'explanation': 'This determines data residency.',
                'options': [
                    {'label': 'US East', 'description': 'Lowest latency for the team.'},
                ],
                'multi_select': False,
                'required': True,
                'response_schema': {'type': 'string'},
            }
            event = {
                'event_id': 'evt-followup',
                'ordinal': 'ord-1',
                'type': 'session_interrupt',
                'session_id': 'ses-followup-resume',
                'root_event_id': 'evt-root',
                'payload': {'interrupt': interrupt},
                'ts': '2026-08-03T12:00:00Z',
            }
            interrupt_response = httpx.Response(
                HTTPStatus.OK,
                headers={'content-type': 'text/event-stream'},
                content=f'id: 4\nevent: session_interrupt\ndata: {json.dumps(event)}\n\n',
            )
            final_response = _invoke_sse_response('ses-followup-resume', 'continued')
            return httpx.Response(
                HTTPStatus.OK,
                headers={'content-type': 'text/event-stream'},
                content=interrupt_response.content + final_response.content,
            )
        if request.url.path.endswith('/interrupts/int-followup-1/responses'):
            body = _json_body(request)
            assert body is not None
            response_bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-followup-resume',
                    'session_id': 'ses-followup-resume',
                    'stream_position': 5,
                },
            )
        pytest.fail(reason=f'unexpected request: {request.method} {request.url}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='followup-agent', client=client)

    response = agent.allow_followup_questions(input_handler=input_handler).invoke('hello')

    assert response.response == 'continued'
    assert handled[0][0].question == question_text
    assert handled[0][1] == {'type': 'string'}
    assert response_bodies == [
        {
            'answer': {'kind': 'free_text', 'value': 'US East'},
            'responded_by': 'sdk-user',
            'surface': 'sdk',
        }
    ]


def test_followup_capability_coexistence_matrix_completes_one_run() -> None:
    """One lineage keeps skills, app tools, dependencies, follow-up, and structured output."""
    calls: list[tuple[str, str, dict[str, object] | None]] = []
    executed: list[tuple[str, object]] = []
    dependency_prompts: list[str] = []
    followups: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        calls.append((request.method, request.url.path, body))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-coexist', 'stream_position': 0},
            )
        if request.url.path == '/v1/sessions/ses-coexist/events':
            return _coexistence_sse_response()
        if request.url.path == '/v1/sessions/ses-coexist/tools/results':
            return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})
        if request.url.path.endswith('/interrupts/tool-argument-coexist/responses'):
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-coexist',
                    'session_id': 'ses-coexist',
                    'stream_position': 5,
                },
            )
        if request.url.path.endswith('/interrupts/followup-coexist/responses'):
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-coexist',
                    'session_id': 'ses-coexist',
                    'stream_position': 5,
                },
            )
        pytest.fail(reason=f'unexpected request: {request.method} {request.url}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='coexist-agent', client=client)
    agent.attach_skill('skill-coexistence-290')

    @agent.toolify(name='inspect_service')
    def inspect_service() -> str:
        executed.append(('inspect_service', None))
        return 'billing-api'

    @agent.toolify(name='prepare_deployment')
    @depends_on_tool(inspect_service, arg_name='service')
    @depends_on_interrupt(
        arg_name='ticket_id',
        input_handler=lambda prompt: dependency_prompts.append(prompt) or 'DEP-290',
        prompt='Deployment ticket: ',
    )
    def prepare_deployment(service: str, ticket_id: str) -> str:
        executed.append(('prepare_deployment', (service, ticket_id)))
        return f'{service}:{ticket_id}'

    _ = prepare_deployment
    response = agent.allow_followup_questions(
        input_handler=lambda question, _schema: followups.append(question.question) or 'US East',
        response_schema={'type': 'string'},
    ).invoke('run matrix', structured_output=_StructuredSummary)

    assert response.result == _StructuredSummary(answer='typed')
    assert executed == [
        ('inspect_service', None),
        ('prepare_deployment', ('billing-api', 'DEP-290')),
    ]
    assert dependency_prompts == ['Deployment ticket: ']
    assert followups == ['Which region should I use?']
    invoke_body = calls[0][2]
    assert invoke_body is not None
    assert invoke_body['attached_skill_ids'] == ['skill-coexistence-290']
    tools = cast('list[dict[str, object]]', invoke_body['tools'])
    prepare = next(tool for tool in tools if tool['name'] == 'prepare_deployment')
    assert prepare['tool_dependencies'] == [{'tool_name': 'inspect_service', 'arg_name': 'service'}]
    assert prepare['interrupt_dependencies'] == [
        {
            'arg_name': 'ticket_id',
            'prompt_source': 'authored',
            'question': 'Deployment ticket: ',
            'response_schema': {'type': 'string'},
        }
    ]
    assert invoke_body['output_schema']
    followup_config = cast('dict[str, object]', invoke_body['followup_questions'])
    assert followup_config['response_schema'] == {'type': 'string'}
    assert any('/tools/results' in path for _, path, _ in calls)
    assert any('/interrupts/tool-argument-coexist/responses' in path for _, path, _ in calls)
    assert any('/interrupts/followup-coexist/responses' in path for _, path, _ in calls)


def test_agent_generated_interrupt_prompt_serializes_as_generation_metadata() -> None:
    """Only the singleton requests generation; an equal ordinary string stays authored."""
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-agent-generated-wire', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-agent-generated-wire', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='agent-generated-wire', client=client)

    @agent.toolify(name='generated_prompt')
    @depends_on_interrupt('answer', lambda _prompt: 'yes', prompt=AgentGenerated)
    def generated_prompt(answer: bool) -> bool:  # noqa: FBT001 - bool drives wire schema.
        return answer

    @agent.toolify(name='literal_prompt')
    @depends_on_interrupt('answer', lambda _prompt: 'yes', prompt=str(AgentGenerated))
    def literal_prompt(answer: bool) -> bool:  # noqa: FBT001 - bool drives wire schema.
        return answer

    _ = generated_prompt, literal_prompt
    _ = agent.invoke('serialize prompts')

    tools = cast('list[dict[str, object]]', bodies[0]['tools'])
    generated = next(tool for tool in tools if tool['name'] == 'generated_prompt')
    literal = next(tool for tool in tools if tool['name'] == 'literal_prompt')
    assert generated['interrupt_dependencies'] == [
        {
            'arg_name': 'answer',
            'prompt_source': 'agent_generated',
            'question': '',
            'response_schema': {'type': 'boolean'},
        }
    ]
    assert literal['interrupt_dependencies'] == [
        {
            'arg_name': 'answer',
            'prompt_source': 'authored',
            'question': str(AgentGenerated),
            'response_schema': {'type': 'boolean'},
        }
    ]


def test_agent_invoke_serializes_ultra_as_a_model_directive() -> None:
    """The Ultra tier must never be mistaken for an exact provider model id."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-ultra', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-ultra', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='ultra-agent', client=client)

    _ = agent.invoke('hello', model='ultra')

    first_call = calls[0]
    assert first_call is not None
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert run_config['model_directive'] == 'ultra'
    assert 'model' not in run_config


def test_agent_invoke_serializes_exact_model_choice_as_force_directive() -> None:
    """An explicit model id is distinguishable from the automatic SDK default."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-exact-model', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-exact-model', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='exact-model-agent', client=client)

    _ = agent.invoke(
        'hello',
        model=ModelConfig(response=ModelChoice(model_id='synthetic-model-id')),
    )

    first_call = calls[0]
    assert first_call is not None
    assert first_call['model'] == 'synthetic-model-id'
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert run_config['model_directive'] == 'force'


def test_agent_invoke_serializes_named_system_and_planning_model_choices() -> None:
    """Planning and system-model choices survive alongside the response override."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-system-models', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-system-models', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='system-model-agent', client=client)

    _ = agent.invoke(
        'hello',
        model=ModelConfig(
            response=ModelChoice(model_id='synthetic-model-id'),
            thinking=ModelChoice(tier='max'),
            compose_argument=ModelChoice(tier='ultra'),
            repl=ModelChoice(model_id='synthetic-repl-model'),
            planning=PlanningChoice(tier='deep'),
        ),
    )

    first_call = calls[0]
    assert first_call is not None
    assert first_call['model'] == 'synthetic-model-id'
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert run_config['model_directive'] == 'force'
    assert run_config['system_model_choices'] == {
        'thinking': {'tier': 'max'},
        'compose_argument': {'tier': 'ultra'},
        'repl': {'model_id': 'synthetic-repl-model'},
    }
    assert run_config['planning'] == {'tier': 'deep'}


def test_agent_stream_limits_request_to_targeted_tools() -> None:
    """Studio tool targeting ships only the explicitly selected agent tools."""
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body is not None:
            request_bodies.append(body)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-targeted-stream', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-targeted-stream', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='targeted-agent', client=client)
    _ = agent.add_tool(lambda: {'answer': 'first'}, name='first_tool')
    _ = agent.add_tool(lambda: {'answer': 'second'}, name='second_tool')

    events = list(agent.stream('hello', targeted_tools=['second_tool']))

    assert events[-1].event_type == 'final'
    tools = cast('list[dict[str, object]]', request_bodies[0]['tools'])
    assert [tool['name'] for tool in tools] == ['second_tool']


def test_agent_stream_includes_dependencies_of_targeted_tool() -> None:
    """Targeting a final tool keeps the dependency graph executable."""
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body is not None:
            request_bodies.append(body)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-targeted-dependencies', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-targeted-dependencies', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='targeted-agent', client=client)

    @agent.toolify()
    def load_context() -> dict[str, str]:
        return {'answer': 'ready'}

    @agent.toolify().depends_on_tool(load_context, 'context')
    def final_report(context: dict[str, str]) -> dict[str, str]:
        return context

    _ = final_report
    events = list(agent.stream('hello', targeted_tools=['final_report']))

    assert events[-1].event_type == 'final'
    tools = cast('list[dict[str, object]]', request_bodies[0]['tools'])
    assert [tool['name'] for tool in tools] == ['load_context', 'final_report']


def test_agent_invoke_stream_response_consumes_streaming_transport() -> None:
    """stream_response=True keeps a blocking invoke shape while using SSE transport."""
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-stream-response', 'stream_position': 0},
            )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 1\nevent: final\n'
                'data: {"event_id":"evt-1","ordinal":"ord-1","type":"final",'
                '"session_id":"ses-stream-response","root_event_id":"evt-root",'
                '"thread_id":"thr-stream-response","payload":{"content":"streamed"},'
                '"ts":"2026-07-04T12:00:00Z"}\n\n'
            ),
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='stream-response-agent', client=client)

    response = agent.invoke(
        'hello',
        stream_response=True,
        options=RunOptions(thread_id='thr-stream-response'),
    )

    assert seen_paths == ['/v1/invoke', '/v1/sessions/ses-stream-response/events']
    assert response.response == 'streamed'
    assert response.thread_id == 'thr-stream-response'


def test_agent_force_final_tool_requires_final_tool() -> None:
    """Agent.invoke mirrors v1 validation when forcing a final tool without one."""
    agent = Agent(name='flag-agent', api_key='test-key', base_url='http://testserver')

    with pytest.raises(ValueError, match='force_final_tool=True requires at least one tool'):
        agent.invoke('hello', force_final_tool=True)


def test_agent_ainvoke_accepts_verbose_and_force_final_tool_kwargs() -> None:
    """Agent.ainvoke exposes the same v1 flags as invoke."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-agent-async', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-agent-async', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='async-agent', client=client)
    _ = agent.add_tool(lambda: {'answer': 'ok'}, name='final_answer', final_tool=True)

    response = asyncio.run(agent.ainvoke('hello', force_final_tool=True, verbose=True))

    assert response.response == 'done'


def test_swarm_invoke_accepts_verbose_and_force_final_tool_kwargs() -> None:
    """Swarm.invoke accepts the v1 invocation flags and validates final-output routing."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-swarm-flags', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-swarm-flags', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='member', client=client)
    swarm = Swarm(name='flag-swarm', agents=[agent], client=client)
    _ = swarm.add_tool(lambda: {'answer': 'ok'}, name='final_answer', final_tool=True)

    response = swarm.invoke('hello', force_final_tool=True, verbose=True)
    async_response = asyncio.run(swarm.ainvoke('hello', force_final_tool=True, verbose=True))

    assert response.response == 'done'
    assert async_response.response == 'done'


def test_swarm_force_final_tool_rejects_ambiguous_members() -> None:
    """Swarm force-final validation rejects ambiguous member final tools."""
    first = Agent(name='first', api_key='test-key', base_url='http://testserver')
    second = Agent(name='second', api_key='test-key', base_url='http://testserver')
    _ = first.add_tool(lambda: 'one', name='first_final', final_tool=True)
    _ = second.add_tool(lambda: 'two', name='second_final', final_tool=True)
    swarm = Swarm(name='ambiguous', agents=[first, second])

    with pytest.raises(ValueError, match='ambiguous'):
        swarm.invoke('hello', force_final_tool=True)


def test_swarm_validation_rejects_ambiguous_member_final_tools() -> None:
    """Validation rejects multiple final-tool owners without a designated agent."""
    first = Agent(name='first', api_key='test-key', base_url='http://testserver')
    second = Agent(name='second', api_key='test-key', base_url='http://testserver')
    _ = first.add_tool(lambda: 'one', name='first_final', final_tool=True)
    _ = second.add_tool(lambda: 'two', name='second_final', final_tool=True)
    swarm = Swarm(name='ambiguous-validation', agents=[first, second])

    with pytest.raises(ValueError, match='final') as exc_info:
        swarm.validate_tool_configuration()

    message = str(exc_info.value)
    assert 'Ambiguous final_tool ownership' in message
    assert 'first' in message
    assert 'second' in message


def test_swarm_validation_allows_designated_member_final_tool_owner() -> None:
    """Validation allows multiple member final tools with one designated agent."""
    first = Agent(
        name='first',
        api_key='test-key',
        base_url='http://testserver',
        use_as_final_output=True,
    )
    second = Agent(name='second', api_key='test-key', base_url='http://testserver')
    _ = first.add_tool(lambda: 'one', name='first_final', final_tool=True)
    _ = second.add_tool(lambda: 'two', name='second_final', final_tool=True)
    swarm = Swarm(name='designated-validation', agents=[first, second])

    swarm.validate_tool_configuration()


def test_swarm_validation_allows_single_undesignated_member_final_tool_owner() -> None:
    """Validation allows one member final-tool owner without a designation."""
    agent = Agent(name='only', api_key='test-key', base_url='http://testserver')
    _ = agent.add_tool(lambda: 'result', name='only_final', final_tool=True)
    swarm = Swarm(name='single-owner-validation', agents=[agent])

    swarm.validate_tool_configuration()


def test_agent_structured_output_builder_coerces_response_result() -> None:
    """Agent.structured_output(...).invoke returns the requested model as result."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-structured', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-structured', '{"answer":"typed"}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='structured-agent', client=client)

    response = agent.structured_output(model=_StructuredSummary).invoke('hello', verbose=True)
    async_response = asyncio.run(
        agent.structured_output(model=_StructuredSummary).ainvoke('hello', verbose=True)
    )

    assert response.result == _StructuredSummary(answer='typed')
    assert async_response.result == _StructuredSummary(answer='typed')


@pytest.mark.parametrize('builder', [False, True])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_agent_structured_output_builder_streams_canonical_events(
    *,
    builder: bool,
    asynchronous: bool,
) -> None:
    """A structured builder exposes live events while retaining its output schema."""
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body is not None:
            request_bodies.append(body)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-structured-stream', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-structured-stream', '{"answer":"typed"}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='structured-stream-agent', client=client)

    if asynchronous:

        async def consume() -> list[str]:
            stream = (
                agent.structured_output(model=_StructuredSummary).astream('hello')
                if builder
                else agent.astream('hello', structured_output=_StructuredSummary)
            )
            return [event.event_type async for event in stream]

        event_types = asyncio.run(consume())
    else:
        stream = (
            agent.structured_output(model=_StructuredSummary).stream('hello')
            if builder
            else agent.stream('hello', structured_output=_StructuredSummary)
        )
        event_types = [event.event_type for event in stream]

    assert event_types == ['final']
    assert request_bodies[0]['output_schema'] == {
        'name': '_StructuredSummary',
        'schema': _StructuredSummary.model_json_schema(),
    }
    assert request_bodies[0]['force_final_tool'] is True
    assert request_bodies[0]['final_tool_ids'] == ['_StructuredSummary']
    tools = cast('list[dict[str, object]]', request_bodies[0]['tools'])
    assert tools[0]['model_constructor'] is True


def test_agent_time_travel_thread_stream_uses_bound_runtime_contract() -> None:
    """Agent rewind streams through its bound tools/options on the same thread."""
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == '/v1/threads/thr-rewind/time-travel':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-rewind',
                    'session_id': 'ses-rewind-agent',
                    'stream_position': 0,
                },
            )
        return _invoke_sse_response('ses-rewind-agent', 'rewritten')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='rewind-agent', client=client)

    events = list(
        agent.time_travel_thread_stream(
            'thr-rewind',
            checkpoint_id='ses-checkpoint',
            message='replace the prompt',
            origin='studio',
        ),
    )

    assert [event.event_type for event in events] == ['final']
    assert paths == [
        '/v1/threads/thr-rewind/time-travel',
        '/v1/sessions/ses-rewind-agent/events',
    ]


def test_structured_output_builder_forwards_per_call_runtime_options() -> None:
    """Structured-output invocables keep v1 per-invoke option kwargs."""
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body is not None:
            request_bodies.append(body)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-structured-options', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-structured-options', '{"answer":"typed"}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='structured-options-agent', client=client)
    _ = agent.add_tool(lambda: 'selected', name='selected')
    _ = agent.add_tool(lambda: 'unselected', name='unselected')

    response = agent.structured_output(model=_StructuredSummary).invoke(
        'hello',
        targeted_tools=['selected'],
        model='fast',
        system_tools_config={'allowed_tools': []},
        orchestration_config={'allow_reevaluate_loop': True},
        options=RunOptions(thread_id='thr-structured-options'),
    )

    assert response.result == _StructuredSummary(answer='typed')
    assert request_bodies[0]['run_config'] == {
        'user_id': 'sdk-user',
        'thread_id': 'thr-structured-options',
        'agent_id': 'structured-options-agent',
        'model_directive': 'fast',
    }
    assert request_bodies[0]['output_schema'] == {
        'name': '_StructuredSummary',
        'schema': _StructuredSummary.model_json_schema(),
    }
    shipped_tools = cast('list[dict[str, object]]', request_bodies[0]['tools'])
    shipped_tool_names = {tool['name'] for tool in shipped_tools}
    assert 'selected' in shipped_tool_names
    assert 'unselected' not in shipped_tool_names


def test_agent_structured_output_builder_raises_for_invalid_model_output() -> None:
    """Structured output validation fails loudly when the final response mismatches."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-invalid-structured', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-invalid-structured', '{"title":"wrong"}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='structured-agent', client=client)

    with pytest.raises(ValueError, match='structured output'):
        agent.structured_output(model=_StructuredSummary).invoke('hello')


def test_agent_events_builder_routes_stream_events_and_returns_final_response() -> None:
    """Agent.events(...).invoke consumes v2 stream events and routes matching payloads."""
    routed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-events', 'stream_position': 0},
            )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 1\nevent: status_message_chunk\n'
                'data: {"event_id":"evt-1","ordinal":"ord-1","type":"status_message_chunk",'
                '"session_id":"ses-events","root_event_id":"evt-root","payload":{"message":"hi"},'
                '"ts":"2026-07-04T12:00:00Z"}\n\n'
                'id: 2\nevent: final\n'
                'data: {"event_id":"evt-2","ordinal":"ord-2","type":"final",'
                '"session_id":"ses-events","root_event_id":"evt-root",'
                '"payload":{"content":"done"},"ts":"2026-07-04T12:00:00Z"}\n\n'
            ),
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='event-agent', client=client)

    response = agent.events(include='status_message_chunk', on_event=routed.append).invoke('hello')

    assert response.response == 'done'
    assert [payload['category'] for payload in routed] == ['status_message_chunk']


def test_swarm_events_builder_rejects_empty_stream() -> None:
    """Swarm.events(...).invoke fails clearly when no final stream event arrives."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-empty-events', 'stream_position': 0},
            )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content='',
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='member', client=client)
    swarm = Swarm(name='event-swarm', agents=[agent], client=client)

    with pytest.raises(ValueError, match='no events'):
        swarm.events().invoke('hello')


def test_agent_batch_preserves_order_and_forwards_v1_kwargs() -> None:
    """Agent.batch invokes each input and preserves result order."""
    seen_messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if request.url.path == '/v1/invoke':
            assert body is not None
            messages = cast('list[dict[str, object]]', body['messages'])
            content = str(messages[-1]['content'])
            seen_messages.append(content)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': f'ses-{content}', 'stream_position': 0},
            )
        session_id = request.url.path.split('/')[-2]
        return _invoke_sse_response(session_id, session_id)

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='batch-agent', client=client)
    _ = agent.add_tool(lambda: {'answer': 'ok'}, name='final_answer', final_tool=True)

    responses = agent.batch(['one', 'two'], max_concurrency=1, force_final_tool=True, verbose=True)
    async_responses = asyncio.run(
        agent.abatch(['three', 'four'], max_concurrency=1, force_final_tool=True, verbose=True)
    )

    assert [response.response for response in responses] == ['ses-one', 'ses-two']
    assert [response.response for response in async_responses] == ['ses-three', 'ses-four']
    assert seen_messages == ['one', 'two', 'three', 'four']


def test_swarm_batch_rejects_invalid_max_concurrency() -> None:
    """Agent and Swarm batch helpers mirror v1 max_concurrency validation."""
    agent = Agent(name='member', api_key='test-key', base_url='http://testserver')
    swarm = Swarm(name='batch-swarm', agents=[agent])

    with pytest.raises(ValueError, match='max_concurrency'):
        agent.batch(['hello'], max_concurrency=0)

    with pytest.raises(ValueError, match='max_concurrency'):
        asyncio.run(swarm.abatch(['hello'], max_concurrency=0))


def test_agent_toolify_registers_callable_metadata() -> None:
    """The v1 decorator authoring pattern remains available without LangChain types."""
    agent = Agent(name='tool-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify(description='Look up a case.')
    def lookup_case(case_id: str) -> dict[str, str]:
        return {'case_id': case_id}

    tool = agent.list_tools()[0]

    assert lookup_case('case-1') == {'case_id': 'case-1'}
    assert [tool.name for tool in agent.list_tools()] == ['lookup_case']
    assert tool.description == 'Look up a case.'


def test_agent_toolify_returns_fluent_dependency_builder() -> None:
    """Agent.toolify supports v1 fluent dependency declaration before registration."""
    agent = Agent(name='fluent-agent', api_key='test-key', base_url='http://testserver')

    def hardware_scanner() -> dict[str, str]:
        return {'cpu': 'arm64'}

    @(
        agent.toolify(description='Build an install plan.')
        .depends_on_tool(
            hardware_scanner,
            'system_profile',
        )
        .depends_on_private_data('customer_name', 'customer')
    )
    def build_plan(system_profile: dict[str, str], customer: str) -> dict[str, object]:
        return {'system_profile': system_profile, 'customer': customer}

    tool_dependencies = cast(
        'list[ToolDependency]',
        getattr(build_plan, TOOL_DEPENDENCIES_ATTR),
    )
    private_dependencies = cast(
        'list[PrivateDataDependency]',
        getattr(build_plan, PRIVATE_DATA_DEPENDENCIES_ATTR),
    )
    tool = agent.list_tools()[0]

    assert build_plan({'cpu': 'x86_64'}, 'Maria') == {
        'system_profile': {'cpu': 'x86_64'},
        'customer': 'Maria',
    }
    assert tool.name == 'build_plan'
    assert tool.description == 'Build an install plan.'
    assert tool_dependencies[0].arg_name == 'system_profile'
    assert tool_dependencies[0].tool_name == 'hardware_scanner'
    assert private_dependencies[0].arg_name == 'customer'
    assert private_dependencies[0].data_key == 'customer_name'


def test_swarm_toolify_uses_same_fluent_builder_as_agent() -> None:
    """Swarm.toolify registers swarm-local tools and dependency metadata."""
    swarm = Swarm(name='fluent-swarm')

    def load_context() -> str:
        return 'context'

    @swarm.toolify(name='coordinate_team').depends_on_tool(load_context, 'context')
    def coordinate(context: str) -> str:
        return context

    tool_dependencies = cast(
        'list[ToolDependency]',
        getattr(coordinate, TOOL_DEPENDENCIES_ATTR),
    )
    tool = swarm.list_tools()[0]

    assert coordinate('ready') == 'ready'
    assert tool.name == 'coordinate_team'
    assert tool_dependencies[0].arg_name == 'context'
    assert tool_dependencies[0].tool_name == 'load_context'


def test_agent_toolify_preserves_pydantic_class_identity() -> None:
    """Class toolification registers a tool and returns the original model class."""
    agent = Agent(name='class-tool-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify(name='diagnostic_result', description='Return a diagnostic result.')
    class DiagnosticResult(BaseModel):
        status: str

    tool = agent.list_tools()[0]

    assert isinstance(DiagnosticResult(status='ok'), DiagnosticResult)
    # `tool_id` is set on the class by the decorator, so pyright cannot see it.
    assert getattr(DiagnosticResult, 'tool_id') == 'diagnostic_result'  # noqa: B009
    assert tool.name == 'diagnostic_result'
    assert tool.description == 'Return a diagnostic result.'
    assert tool.input_schema['properties']['status']['type'] == 'string'


def test_compile_tools_refreshes_schema_after_outer_dependency_decorator() -> None:
    """V1 outer dependency decorators are reflected by lazy tool compilation."""
    agent = Agent(name='lazy-schema-agent', api_key='test-key', base_url='http://testserver')

    @depends_on_private_data(data_key='account', arg_name='account')
    @agent.toolify(name='lookup', description='Lookup an account.')
    def lookup(account: dict[str, object], question: str) -> str:
        return f'{account}:{question}'

    _ = lookup
    compiled = agent.compile_tools()[0]

    assert compiled.input_schema == {
        'type': 'object',
        'properties': {'question': {'type': 'string'}},
        'additionalProperties': False,
        'required': ['question'],
    }


def test_compile_tools_hides_pydantic_model_dependency_fields() -> None:
    """Final-model dependency fields are SDK-injected and absent from provider tools."""
    agent = Agent(name='model-schema-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify(
        name='final_answer', description='Return the answer.', final_tool=True
    ).depends_on_private_data(
        data_key='account',
        arg_name='account',
    )
    class FinalAnswer(BaseModel):
        account: dict[str, object]
        answer: str

    _ = FinalAnswer
    compiled = agent.compile_tools()[0]

    assert compiled.input_schema['properties'] == {'answer': {'title': 'Answer', 'type': 'string'}}
    assert compiled.input_schema['required'] == ['answer']


def test_public_tool_dependency_guidance_survives_compilation_and_wire_projection() -> None:
    """A public injected tool result may retain guidance without exposing its field."""
    agent = Agent(name='public-guidance-agent', api_key='test-key', base_url='http://testserver')

    class Source(BaseModel):
        value: int

    final_tool = (
        agent.toolify(final_tool=True)
        .depends_on_tool(Source, 'source')
        .depends_on_private_data(data_key='secret', arg_name='secret')
    )

    @final_tool
    class FinalAnswer(BaseModel):
        """Public dependency guidance: retain this developer-authored overview."""

        source: Source = Field(description='Use this public source to verify the conclusion.')
        secret: str = Field(description='PRIVATE-DESCRIPTION-MUST-NOT-LEAK')
        answer: str

    _ = FinalAnswer
    compiled = agent.compile_tools()
    final = next(tool for tool in compiled if tool.name == 'FinalAnswer')
    wire = _agent_spec(agent, agent.name)
    wire_tools = cast('list[dict[str, object]]', wire['tools'])
    wire_final = next(tool for tool in wire_tools if tool['name'] == 'FinalAnswer')
    schema = cast('dict[str, object]', wire_final['input_schema'])

    assert 'source' not in final.input_schema['properties']
    assert 'secret' not in final.input_schema['properties']
    assert final.input_schema['description'] == schema['description']
    assert 'Use this public source to verify the conclusion.' in cast('str', schema['description'])
    assert 'PRIVATE-DESCRIPTION-MUST-NOT-LEAK' not in cast('str', schema['description'])
    assert _agent_spec(agent, agent.name) == wire


def test_function_dependency_prerequisites_survive_wire_projection_without_private_context() -> (
    None
):
    """A callable's hidden public binding still tells the model which producer must finish."""
    agent = Agent(name='function-prerequisites', api_key='test-key', base_url='http://testserver')

    @agent.toolify()
    def source_value() -> int:
        return 7

    dependent = (
        agent.toolify()
        .depends_on_tool(source_value, 'source')
        .depends_on_private_data(data_key='private-context-key', arg_name='secret')
    )

    @dependent
    def calculate(source: int, secret: str, amount: int) -> int:
        return source + amount + len(secret)

    _ = calculate
    wire = _agent_spec(agent, agent.name)
    tool = next(
        item
        for item in cast('list[dict[str, object]]', wire['tools'])
        if item['name'] == 'calculate'
    )
    schema = cast('dict[str, object]', tool['input_schema'])
    description = str(schema.get('description', ''))
    assert 'source_value' in description
    assert 'succeeds' in description
    assert 'source' not in cast('dict[str, object]', schema['properties'])
    assert 'secret' not in json.dumps(schema)
    assert 'private-context-key' not in json.dumps(schema)
    assert _agent_spec(agent, agent.name) == wire


def test_compile_tools_expands_nested_pydantic_models_into_dependency_graph() -> None:
    """Nested output models remain independently executable, as in the v1 graph."""
    agent = Agent(name='nested-model-agent', api_key='test-key', base_url='http://testserver')

    class LeafResult(BaseModel):
        value: str

    class BranchResult(BaseModel):
        leaf: LeafResult
        note: str

    @agent.toolify(final_tool=True)
    class FinalResult(BaseModel):
        branch: BranchResult
        summary: str

    _ = FinalResult
    compiled = agent.compile_tools()

    assert [tool.name for tool in compiled] == ['LeafResult', 'BranchResult', 'FinalResult']
    assert compiled[0].input_schema['properties'] == {'value': {'title': 'Value', 'type': 'string'}}
    assert compiled[1].input_schema['properties'] == {'note': {'title': 'Note', 'type': 'string'}}
    assert compiled[2].input_schema['properties'] == {
        'summary': {'title': 'Summary', 'type': 'string'}
    }
    branch_dependencies = cast(
        'list[ToolDependency]', getattr(BranchResult, TOOL_DEPENDENCIES_ATTR)
    )
    final_dependencies = cast('list[ToolDependency]', getattr(FinalResult, TOOL_DEPENDENCIES_ATTR))
    assert [(item.arg_name, item.tool_name) for item in branch_dependencies] == [
        ('leaf', 'LeafResult')
    ]
    assert [(item.arg_name, item.tool_name) for item in final_dependencies] == [
        ('branch', 'BranchResult')
    ]


@pytest.mark.parametrize('builder', [False, True])
@pytest.mark.parametrize('asynchronous', [False, True])
def test_structured_output_forces_the_model_as_a_final_tool(
    *,
    builder: bool,
    asynchronous: bool,
) -> None:
    """Structured output compiles the model into a forced final tool, as v1's state compiler did.

    v1 had no "structured output without a forced final tool" state: `structured_output`
    always toolified the model, promoted a tool already built on that same model rather than
    duplicating it, and set force_final_tool. So the schema on the wire is the TOOL's schema,
    and a field the SDK injects - private data, here - is not in it. The model is not asked
    to invent the caller's account id; the SDK supplies it when the tool runs.
    """
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body is not None:
            request_bodies.append(body)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-full-structured', 'stream_position': 0},
            )
        return _invoke_sse_response(
            'ses-full-structured',
            '{"account":{"id":"acct-1"},"answer":"ready"}',
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='full-structured-agent', client=client)

    @agent.toolify(
        name='final_answer', description='Return the answer.', final_tool=True
    ).depends_on_private_data(
        data_key='account',
        arg_name='account',
    )
    class FinalAnswer(BaseModel):
        account: dict[str, object]
        answer: str

    system_policy = SystemToolsConfig(allowed_tools=['fire_trigger'])
    if asynchronous:
        response = asyncio.run(
            agent.structured_output(model=FinalAnswer).ainvoke(
                'hello', system_tools_config=system_policy
            )
            if builder
            else agent.ainvoke(
                'hello', structured_output=FinalAnswer, system_tools_config=system_policy
            )
        )
    else:
        response = (
            agent.structured_output(model=FinalAnswer).invoke(
                'hello', system_tools_config=system_policy
            )
            if builder
            else agent.invoke(
                'hello',
                structured_output=FinalAnswer,
                system_tools_config=system_policy,
                metadata={'structured_output_intent': False},
            )
        )

    assert response.result == FinalAnswer(account={'id': 'acct-1'}, answer='ready')
    body = request_bodies[0]
    assert body['force_final_tool'] is True
    assert body['final_tool_ids'] == ['final_answer']
    assert body['system_tools_config'] == {'allowed_system_tools': ['fire_trigger']}
    tools = cast('list[dict[str, object]]', body['tools'])
    assert len(tools) == 1
    assert tools[0]['model_constructor'] is True
    # the tool's schema, not the model's: the injected private-data field is absent
    output_schema = cast('dict[str, object]', body['output_schema'])
    schema = cast('dict[str, object]', output_schema['schema'])
    properties = cast('dict[str, object]', schema['properties'])
    assert set(properties) == {'answer'}


def test_structured_output_adds_the_model_as_a_final_tool_when_none_exists() -> None:
    """A bare structured_output(model) still runs through a forced final tool.

    This is the case that decides the latency of every structured demo. Without a final tool
    the schema travels as a provider output-format request, and a shape the provider cannot
    express - an open map, or one field name carrying two types - drops the run into a prose
    fallback or a full schema-decomposition pass instead of a single tool call.
    """
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if body is not None:
            request_bodies.append(body)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-bare-structured', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-bare-structured', '{"answer":"ready"}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='bare-structured-agent', client=client)

    class Answer(BaseModel):
        """Answer the question."""

        answer: str

    response = agent.structured_output(model=Answer).invoke('hello')

    assert response.result == Answer(answer='ready')
    body = request_bodies[0]
    assert body['force_final_tool'] is True
    assert body['final_tool_ids'] == ['Answer']
    tools = cast('list[dict[str, object]]', body['tools'])
    assert [tool['name'] for tool in tools] == ['Answer']
    assert tools[0]['model_constructor'] is True


def test_execution_control_accepts_all_instance_control() -> None:
    """depends_on_reevaluate accepts the v1 all-instance mode used by demos."""

    def expose_private_values() -> dict[str, str]:
        return {'secret': 'value'}

    @depends_on_reevaluate(expose_private_values, timing='after', instance_control='all')
    def redaction_proof_report() -> dict[str, str]:
        return {'status': 'ok'}

    controls = cast(
        'list[ExecutionControl]',
        getattr(redaction_proof_report, EXECUTION_CONTROLS_ATTR),
    )

    assert controls[0].instance_control == 'all'


def test_agent_accepts_v1_constructor_compat_options() -> None:
    """Agent stores v1 constructor defaults for memory, private data, and system tools."""
    memory_config = MemoryConfig(level='focus')
    system_tools_config = SystemToolsConfig(
        allowed_tools=['think'],
        approved_compose_argument_targets=['validate_query.query'],
        allow_private_data_placeholders=True,
    )
    orchestration_config = SessionOrchestrationConfig(allow_reevaluate_loop=True)
    skill_spec: dict[str, object] = {
        'skill_id': 'support-triage-v1',
        'name': 'support_triage',
        'description': 'Triage customer support cases.',
        'steps': [{'index': 1, 'action': 'read the customer case'}],
    }
    resource_spec: dict[str, object] = {
        'name': 'support_runbook.txt',
        'mime_type': 'text/plain',
    }

    def lookup_case(case_id: str) -> dict[str, str]:
        return {'case_id': case_id}

    agent = Agent(
        name='compat-agent',
        api_key='test-key',
        base_url='http://testserver',
        memory_config=memory_config,
        private_data=[PrivateData(value='Maria Santos', name='customer_name')],
        system_tools_config=system_tools_config,
        orchestration_config=orchestration_config,
        allow_private_in_system_tools=True,
        use_as_final_output=True,
        included_nested_synthesis=False,
        tools=[lookup_case],
        skills=[skill_spec],
        resources=[resource_spec],
        tags=['support'],
    )

    assert agent.memory_config == memory_config
    assert agent.private_data == {'customer_name': 'Maria Santos'}
    assert agent.system_tools_config == system_tools_config
    assert system_tools_config.to_metadata_patch()['approved_compose_argument_targets'] == [
        'validate_query.query'
    ]
    assert agent.orchestration_config == orchestration_config
    assert agent.resolve_system_tools_config().allow_private_data is True
    assert agent.resolve_orchestration_config().allow_reevaluate_loop is True
    assert agent.use_as_final_output is True
    assert agent.included_nested_synthesis is False
    assert [tool.name for tool in agent.list_tools()] == ['lookup_case']
    assert agent.skills == [
        Skill(
            memory_id='support-triage-v1',
            name='support_triage',
            description='Triage customer support cases.',
            steps=(SkillStep(index=1, action='read the customer case'),),
        )
    ]
    assert agent.resources == [resource_spec]
    assert agent.tags == ['support']


def test_agent_accepts_private_data_with_object_key_mapping_type() -> None:
    """private_data accepts the wider v1 mapping shape used by demos."""
    private_data = cast('dict[object, object]', {'data_url': 'https://example.test/data.json'})

    agent = Agent(
        name='private-data-agent',
        api_key='test-key',
        base_url='http://testserver',
        private_data=private_data,
    )
    agent.private_data = {'sensor_bus_id': 'CAN0'}

    assert agent.private_data == {'sensor_bus_id': 'CAN0'}


def test_agent_and_swarm_expose_hook_mode_and_tool_cache_shims() -> None:
    """Mutable v1 compatibility members remain assignable on public scopes."""
    agent = Agent(name='shim-agent', api_key='test-key', base_url='http://testserver')
    swarm = Swarm(name='shim-swarm')

    agent.hook_execution_mode = 'scope'
    swarm.hook_execution_mode = 'agent'
    setattr(agent, '_compiled_tools_cache', [])  # noqa: B010 - v1 private poke compatibility.
    setattr(agent, '_tools_dirty', False)  # noqa: B010 - v1 private poke compatibility.

    assert agent.hook_execution_mode == 'scope'
    assert swarm.hook_execution_mode == 'agent'
    assert agent.compile_tools() == []
    agent.validate_tool_configuration()
    swarm.validate_tool_configuration()


def test_cron_accepts_tz_alias() -> None:
    """BaseScope.cron accepts the v1 tz= alias."""
    agent = Agent(name='cron-agent', api_key='test-key', base_url='http://testserver')

    job = agent.cron('* * * * *', tz='UTC', mode='local', max_runs=1).invoke('hello')

    try:
        assert str(job.schedule.tz) == 'UTC'
    finally:
        job.stop()


def test_agent_toolify_threads_v1_options_into_registered_tool_metadata() -> None:
    """Agent.toolify keeps v1 tool kwargs visible through list_tools."""

    def before_execute(payload: dict[str, object]) -> object:
        return payload

    def after_execute(payload: dict[str, object]) -> object:
        return payload

    agent = Agent(name='tag-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify(
        name='lookup_customer',
        description='Lookup a customer.',
        permissions=PermissionSet(PermissionFlag.READ),
        destructive=True,
        always_execute=True,
        metadata={'source': 'crm'},
        tags=['customer'],
        before_execute=before_execute,
        after_execute=after_execute,
    )
    def lookup(customer_id: str) -> dict[str, str]:
        return {'customer_id': customer_id}

    tool = agent.list_tools()[0]

    assert lookup('cus-1') == {'customer_id': 'cus-1'}
    assert tool.name == 'lookup_customer'
    assert tool.description == 'Lookup a customer.'
    assert tool.always_execute is True
    assert tool.tags == ('customer', 'read', 'destructive')
    assert tool.metadata == {'source': 'crm', 'permissions': ['read'], 'destructive': True}
    assert tool.before_execute is before_execute
    assert tool.after_execute is after_execute


def test_agent_add_tool_accepts_v1_option_kwargs() -> None:
    """Agent.add_tool stores v1 registration kwargs on the returned tool handle."""
    agent = Agent(name='add-tool-agent', api_key='test-key', base_url='http://testserver')

    def summarize_case(case_id: str) -> dict[str, str]:
        return {'case_id': case_id}

    registered = agent.add_tool(
        summarize_case,
        tags=['case'],
        metadata={'owner': 'support'},
        always_execute=True,
    )

    tool = agent.list_tools()[0]

    assert registered is summarize_case
    assert tool.tags == ('case',)
    assert tool.metadata == {'owner': 'support'}
    assert tool.always_execute is True
    assert tool.target is summarize_case
    assert 'target' not in tool.model_dump(mode='json')


def test_agent_add_toolset_registers_prefixed_handles_and_list_tools() -> None:
    """Decorated toolset methods register through the same tool registry as add_tool."""
    output_schema = {
        'type': 'object',
        'properties': {'posts': {'type': 'array'}},
    }

    @toolset(prefix='reddit')
    class RedditTools:
        @toolify(description='List subreddit posts.', tags=['read'])
        @tool_output(output_schema)
        def list_subreddit_posts(self, subreddit: str) -> dict[str, object]:
            return {'subreddit': subreddit}

        def helper(self) -> str:
            return 'not registered'

    agent = Agent(name='toolset-agent', api_key='test-key', base_url='http://testserver')

    handles = agent.add_toolset(RedditTools())

    assert [tool.name for tool in handles] == ['REDDIT_list_subreddit_posts']
    assert handles[0].description == 'List subreddit posts.'
    assert handles[0].output_schema == output_schema
    assert agent.list_tools()[0] is handles[0]


def test_agent_add_toolset_filters_by_bare_names_and_tags() -> None:
    """Include/exclude filters use bare method names and unioned method/toolset tags."""

    @toolset(prefix='ops', tags=['shared'])
    class OpsTools:
        @toolify(tags=['read'])
        def read_status(self) -> dict[str, str]:
            return {'status': 'ok'}

        @toolify(tags=['write'])
        def update_status(self, value: str) -> dict[str, str]:
            return {'status': value}

        @toolify(permissions=PermissionSet(PermissionFlag.DELETE), destructive=True)
        def delete_status(self) -> dict[str, str]:
            return {'status': 'deleted'}

    included = Agent(name='included', api_key='test-key', base_url='http://testserver')
    excluded = Agent(name='excluded', api_key='test-key', base_url='http://testserver')
    safe = Agent(name='safe', api_key='test-key', base_url='http://testserver')
    shared = Agent(name='shared', api_key='test-key', base_url='http://testserver')

    included_handles = included.add_toolset(OpsTools(), include=['read_status'])
    excluded_handles = excluded.add_toolset(OpsTools(), exclude=['delete_status'])
    safe_handles = safe.add_toolset(OpsTools(), exclude_tags=['destructive'])
    shared_handles = shared.add_toolset(OpsTools(), include_tags=['shared'])

    assert [tool.name for tool in included_handles] == ['OPS_read_status']
    assert {tool.name for tool in excluded_handles} == {
        'OPS_read_status',
        'OPS_update_status',
    }
    assert {tool.name for tool in safe_handles} == {
        'OPS_read_status',
        'OPS_update_status',
    }
    assert {tool.name for tool in shared_handles} == {
        'OPS_delete_status',
        'OPS_read_status',
        'OPS_update_status',
    }


def test_agent_add_toolset_rejects_duplicate_tool_names() -> None:
    """Duplicate full tool names fail at registration time."""

    @toolset(prefix='dup')
    class DuplicateTools:
        @toolify(name='same')
        def first(self) -> str:
            return 'first'

        @toolify(name='same')
        def second(self) -> str:
            return 'second'

    agent = Agent(name='dup-agent', api_key='test-key', base_url='http://testserver')

    with pytest.raises(ValueError, match='duplicate tool name'):
        agent.add_toolset(DuplicateTools())


def test_list_tools_includes_add_tool_and_swarm_toolset_handles() -> None:
    """Agent and Swarm expose registered tool handles through list_tools."""

    @toolset(prefix='demo')
    class DemoTools:
        @toolify(description='Run demo.')
        def run_demo(self) -> str:
            return 'demo'

    def local_tool() -> str:
        return 'local'

    agent = Agent(name='list-agent', api_key='test-key', base_url='http://testserver')
    _ = agent.add_tool(local_tool, name='LOCAL_tool', description='Local tool.')
    agent_handles = agent.add_toolset(DemoTools())

    swarm = Swarm(name='list-swarm', agents=[agent])
    swarm_handles = swarm.add_toolset(DemoTools())

    assert [tool.name for tool in agent.list_tools()] == ['LOCAL_tool', 'DEMO_run_demo']
    assert agent.list_tools()[1] is agent_handles[0]
    assert [tool.name for tool in swarm_handles] == ['DEMO_run_demo']
    assert swarm.list_tools()[0] is swarm_handles[0]


def test_agent_register_mcp_servers_stores_specs() -> None:
    """Agent.register_mcp_servers stores v1 MCPServer specs for later snapshots."""
    first = MCPServer(name='filesystem')
    second = MCPServer(name='browser')
    agent = Agent(name='mcp-agent', api_key='test-key', base_url='http://testserver')

    agent.register_mcp_servers(first)
    agent.register_mcp_servers([second])

    assert agent.list_mcp_servers() == [first, second]


def test_agent_compiles_registered_mcp_tools_for_local_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registered MCP tools join the SDK snapshot and retain their MCP executor."""
    server = MCPServer(name='inventory')
    calls: list[tuple[str, dict[str, object]]] = []

    def list_tools(_server: MCPServer) -> list[dict[str, object]]:
        return [
            {
                'name': 'lookup_item',
                'description': 'Look up one inventory item.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {'sku': {'type': 'string'}},
                    'required': ['sku'],
                },
            }
        ]

    monkeypatch.setattr(MCPServer, 'list_tools', list_tools)

    def call_tool(
        _server: MCPServer,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        calls.append((tool_name, arguments))
        return {'structuredContent': {'sku': arguments['sku'], 'available': True}}

    monkeypatch.setattr(MCPServer, 'call_tool', call_tool)
    agent = Agent(name='mcp-agent', api_key='test-key', base_url='http://testserver')
    agent.register_mcp_servers(server)

    tools = agent.compile_tools()

    assert [tool.name for tool in tools] == ['inventory__lookup_item']
    assert tools[0].input_schema['required'] == ['sku']
    assert tools[0].target(sku='ABC-123') == {
        'structuredContent': {'sku': 'ABC-123', 'available': True}
    }
    assert calls == [('lookup_item', {'sku': 'ABC-123'})]


def test_swarm_accepts_v1_system_prompt() -> None:
    """Swarm accepts and stores the v1 system prompt constructor kwarg."""
    agent = Agent(name='member', api_key='test-key', base_url='http://testserver')

    swarm = Swarm(name='compat-swarm', agents=[agent], system_prompt='Coordinate carefully.')

    assert swarm.system_prompt == 'Coordinate carefully.'


def test_swarm_accepts_late_v1_member_registration() -> None:
    """Swarm can be created empty and populated through the v1 member builder."""
    swarm = Swarm(name='member-swarm', system_prompt='Coordinate members.')

    def load_context() -> dict[str, str]:
        return {'market': 'healthcare'}

    @swarm.member
    @depends_on_tool(load_context, arg_name='context')
    def research_agent() -> Agent:
        return Agent(name='Research Agent', api_key='test-key', base_url='http://testserver')

    research_member = cast('Agent', research_agent)
    editor = Agent(name='Editor Agent', api_key='test-key', base_url='http://testserver')
    registered_editor = swarm.member.depends_on_agent(
        research_member,
        arg_name='research_notes',
    )(editor)

    research_dependencies = cast(
        'list[ToolDependency]',
        getattr(research_member, TOOL_DEPENDENCIES_ATTR),
    )
    editor_dependencies = cast(
        'list[AgentDependency]',
        getattr(editor, TOOL_DEPENDENCIES_ATTR),
    )

    assert swarm.list_agents() == [research_member, editor]
    assert registered_editor is editor
    assert research_dependencies[0].arg_name == 'context'
    assert editor_dependencies[0].arg_name == 'research_notes'
    assert editor_dependencies[0].agent_id == 'Research Agent'


def test_agent_add_skill_registers_and_pins_first_class_skill() -> None:
    """Agent.add_skill mirrors add_tool while registering a stored skill."""
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, _json_body(request)))
        return httpx.Response(
            HTTPStatus.OK,
            json={
                'kind': 'skill',
                'memory_id': 'skill-agent',
                'scope': {
                    'project_id': 'project-sdk',
                    'organization_id': 'org-sdk',
                    'sharing_scope': 'project',
                },
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
            },
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='renewal-agent', client=client)

    skill = agent.add_skill(
        name='Renewal escalation',
        description='Escalate renewal blockers.',
        steps=['Review blockers'],
    )

    assert skill.memory_id == 'skill-agent'
    assert agent.skills == [skill]
    assert agent.attached_skill_ids == ['skill-agent']
    assert calls == [
        (
            'POST',
            '/v1/skills',
            {
                'name': 'Renewal escalation',
                'description': 'Escalate renewal blockers.',
                'steps': [{'index': 1, 'action': 'Review blockers'}],
                'scope': {'agent_id': 'renewal-agent', 'sharing_scope': 'agent'},
            },
        ),
    ]


def test_swarm_add_skill_set_registers_hardcoded_tree() -> None:
    """Swarm.add_skill_set supports hardcoded SkillSet trees like Agent.add_tool."""
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == '/v1/skill-sets':
            body = _json_body(request) or {}
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    'kind': 'skill_set',
                    'id': str(body.get('id') or 'set-renewal'),
                    'scope': {
                        'project_id': 'project-sdk',
                        'organization_id': 'org-sdk',
                        'sharing_scope': 'project',
                    },
                    'name': str(body['name']),
                    'description': str(body['description']),
                    'member_skill_ids': [],
                },
            )
        if request.url.path == '/v1/skills':
            body = _json_body(request) or {}
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    'kind': 'skill',
                    'memory_id': str(body.get('memory_id') or 'skill-renewal'),
                    'scope': {
                        'project_id': 'project-sdk',
                        'organization_id': 'org-sdk',
                        'sharing_scope': 'project',
                    },
                    'name': str(body['name']),
                    'description': str(body['description']),
                    'steps': body['steps'],
                    'preconditions': {},
                    'postconditions': {},
                    'confidence': 1.0,
                    'origin': 'user_defined',
                    'status': 'active',
                    'created_at': '2026-07-05T12:00:00Z',
                    'updated_at': '2026-07-05T12:00:00Z',
                },
            )
        return httpx.Response(
            HTTPStatus.OK,
            json={
                'kind': 'skill_set',
                'id': 'set-renewal',
                'scope': {
                    'project_id': 'project-sdk',
                    'organization_id': 'org-sdk',
                    'sharing_scope': 'project',
                },
                'name': 'Renewal Handling',
                'description': 'Renewal procedures.',
                'member_skill_ids': ['skill-renewal'],
            },
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='renewal-agent', client=client)
    swarm = Swarm(name='renewal-team', agents=[agent], client=client)

    skill_set = swarm.add_skill_set(
        SkillSet(
            id='set-renewal',
            name='Renewal Handling',
            description='Renewal procedures.',
            children=(
                Skill(
                    memory_id='skill-renewal',
                    name='Gather Context',
                    description='Gather facts.',
                    steps=(SkillStep(index=1, action='Collect context'),),
                ),
            ),
        ),
    )

    assert skill_set.id == 'set-renewal'
    assert swarm.skill_sets == [skill_set]
    assert seen_paths == [
        '/v1/skill-sets',
        '/v1/skills',
        '/v1/skill-sets/set-renewal/skills/skill-renewal',
    ]


def test_agent_add_skill_binds_the_skill_to_the_authoring_agent() -> None:
    """An authored skill carries the same identity its own runs execute under.

    The brain decides skill visibility from the stored scope, and an agent-scoped
    skill is visible only to a run whose agent_id equals it. A skill stored with no
    identity stays project-shared: every other agent in the project retrieves it,
    and the binding to the agent that authored it is gone.
    """
    create_bodies: list[dict[str, object]] = []
    invoke_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if request.url.path == '/v1/skills':
            assert body is not None
            create_bodies.append(body)
            return httpx.Response(HTTPStatus.OK, json=_skill_response('skill-agent'))
        if request.url.path == '/v1/invoke':
            assert body is not None
            invoke_bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-skill-scope', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-skill-scope', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='renewal-agent', client=client)

    _ = agent.add_skill(
        name='Renewal escalation',
        description='Escalate renewal blockers.',
        steps=['Review blockers'],
    )
    _ = agent.invoke('escalate the renewal')

    scope = create_bodies[0].get('scope')
    run_config = cast('dict[str, object]', invoke_bodies[0]['run_config'])
    assert scope == {'agent_id': 'renewal-agent', 'sharing_scope': 'agent'}
    assert cast('dict[str, object]', scope)['agent_id'] == run_config['agent_id']


def test_swarm_add_skill_set_binds_the_tree_to_the_authoring_swarm() -> None:
    """A swarm's authored skill set and its skills carry the swarm's own identity."""
    scopes: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request) or {}
        if request.url.path == '/v1/skill-sets':
            scopes['skill_set'] = body.get('scope')
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    'kind': 'skill_set',
                    'id': 'set-renewal',
                    'scope': {'project_id': 'project-sdk', 'sharing_scope': 'swarm'},
                    'name': str(body['name']),
                    'description': str(body['description']),
                    'member_skill_ids': [],
                },
            )
        if request.url.path == '/v1/skills':
            scopes['skill'] = body.get('scope')
            return httpx.Response(HTTPStatus.OK, json=_skill_response('skill-renewal'))
        return httpx.Response(
            HTTPStatus.OK,
            json={
                'kind': 'skill_set',
                'id': 'set-renewal',
                'scope': {'project_id': 'project-sdk', 'sharing_scope': 'swarm'},
                'name': 'Renewal Handling',
                'description': 'Renewal procedures.',
                'member_skill_ids': ['skill-renewal'],
            },
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='renewal-agent', client=client)
    swarm = Swarm(name='renewal-team', agents=[agent], client=client)

    _ = swarm.add_skill_set(
        SkillSet(
            id='set-renewal',
            name='Renewal Handling',
            description='Renewal procedures.',
            children=(
                Skill(
                    memory_id='skill-renewal',
                    name='Gather Context',
                    description='Gather facts.',
                    steps=(SkillStep(index=1, action='Collect context'),),
                ),
            ),
        ),
    )

    expected = {'swarm_id': 'renewal-team', 'sharing_scope': 'swarm'}
    assert scopes['skill_set'] == expected
    assert scopes['skill'] == expected


def _skill_response(memory_id: str) -> dict[str, object]:
    return {
        'kind': 'skill',
        'memory_id': memory_id,
        'scope': {'project_id': 'project-sdk', 'sharing_scope': 'project'},
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


def test_attach_and_use_skills_ride_on_the_invoke_body_not_run_config() -> None:
    """Skills are invocation-local, and the emitted run_config satisfies the real contract.

    RunConfig forbids unknown fields, so skill options carried inside it made every
    skill-bearing invoke fail validation at the server. Validating the emitted run_config
    against the shared contract model - rather than against a payload this test also
    authored - is what keeps that unrepresentable.
    """
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        if request.url.path == '/v1/invoke':
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-skills', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-skills', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='skill-agent', client=client)
    agent.attach_skill('skill-pinned')
    agent.use_skills('renewal legal escalation', limit=_AUTO_SKILLS_LIMIT)

    _ = agent.invoke('hello', options=RunOptions(thread_id='thr-skills', user_id='usr-sdk'))

    body = bodies[0]
    assert body['attached_skill_ids'] == ['skill-pinned']
    assert body['auto_skills'] is True
    assert body['auto_skills_query'] == 'renewal legal escalation'
    assert body['auto_skills_limit'] == _AUTO_SKILLS_LIMIT
    run_config = cast('dict[str, object]', body['run_config'])
    assert RunConfig.model_validate(run_config) is not None
    assert run_config == {
        'user_id': 'usr-sdk',
        'thread_id': 'thr-skills',
        'agent_id': 'skill-agent',
    }


def _json_body(request: httpx.Request) -> dict[str, object] | None:
    if not request.content:
        return None
    return cast('dict[str, object]', json.loads(request.content.decode('utf-8')))


def _resource_response(
    *,
    binding_type: str = 'portal',
    sharing_scope: str = 'project',
    agent_id: str | None = None,
) -> dict[str, object]:
    return {
        'memory_id': 'resource-sdk',
        'resource_id': 'resource-sdk',
        'resource_thread_id': 'rth-0123456789abcdef0123456789abcdef',
        'scope': {
            'project_id': 'project-sdk',
            'organization_id': 'org-sdk',
            'agent_id': agent_id,
            'swarm_id': None,
            'sharing_scope': sharing_scope,
        },
        'name': 'bound-rollout-runbook.txt',
        'description': None,
        'media_type': 'text/plain',
        'content_hash': '0' * 64,
        'size_bytes': 16,
        'status': 'registered',
        'processing_status': 'pending',
        'tags': ['rollout', 'runbook'],
        'metadata': {},
        'binding_type': binding_type,
        'deduplicated': False,
        'created_at': '2026-07-13T12:00:00Z',
        'updated_at': '2026-07-13T12:00:00Z',
    }


def _invoke_sse_response(session_id: str, content: str) -> httpx.Response:
    event: dict[str, object] = {
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


def _tool_request_sse_response() -> httpx.Response:
    tool_event: dict[str, object] = {
        'event_id': 'evt-tool-start',
        'ordinal': 'ord-1',
        'type': 'system_tool_start',
        'session_id': 'ses-tool-execution',
        'root_event_id': 'evt-root',
        'correlation_id': 'call-sdk-1',
        'actor': 'tool:lookup_account',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': {
            'stage': 'tool_start',
            'tool_name': 'lookup_account',
            'tool_call': {
                'call_id': 'call-sdk-1',
                'spec_ref': {
                    'tool_id': 'lookup_account',
                    'namespace': 'sdk',
                    'version': 'v1',
                },
                'arguments': {'question': 'status?'},
                'lineage': {
                    'session_id': 'ses-tool-execution',
                    'invocation_id': 'inv-1',
                },
            },
        },
        'ts': '2026-07-04T12:00:00Z',
    }
    final_event: dict[str, object] = {
        'event_id': 'evt-final',
        'ordinal': 'ord-2',
        'type': 'final',
        'session_id': 'ses-tool-execution',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-final',
                'role': 'assistant',
                'content': 'Account is active.',
                'ts': '2026-07-04T12:00:01Z',
            },
            'usage': {'input_tokens': 4, 'output_tokens': 3},
            'tool_calls': {'count': 1, 'names': ['lookup_account']},
        },
        'ts': '2026-07-04T12:00:01Z',
    }
    phase_event = _tool_phase_started_event('ses-tool-execution', ordinal='ord-2')
    content = (
        f'id: 1\nevent: system_tool_start\ndata: {json.dumps(tool_event)}\n\n'
        f'id: 2\nevent: system_tool_chunk\ndata: {json.dumps(phase_event)}\n\n'
        f'id: 3\nevent: final\ndata: {json.dumps(final_event)}\n\n'
    )
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=content,
    )


def _parallel_tool_request_sse_response() -> httpx.Response:
    events: list[str] = []
    for position, tool_name in enumerate(('first_tool', 'second_tool'), start=1):
        call_id = f'call-{tool_name}'
        event = {
            'event_id': f'evt-{tool_name}',
            'ordinal': f'ord-{position}',
            'type': 'system_tool_start',
            'session_id': 'ses-parallel-tools',
            'root_event_id': 'evt-root',
            'correlation_id': call_id,
            'actor': f'tool:{tool_name}',
            'mode': 'hosted',
            'trigger_chain_depth': 0,
            'job_depth': 0,
            'job_index': 0,
            'payload': {
                'stage': 'tool_start',
                'tool_name': tool_name,
                'tool_call': {
                    'call_id': call_id,
                    'spec_ref': {
                        'tool_id': tool_name,
                        'namespace': 'sdk',
                        'version': 'v1',
                    },
                    'arguments': {},
                    'lineage': {
                        'session_id': 'ses-parallel-tools',
                        'invocation_id': 'inv-parallel-tools',
                    },
                },
            },
            'ts': '2026-07-04T12:00:00Z',
        }
        events.append(f'id: {position}\nevent: system_tool_start\ndata: {json.dumps(event)}\n\n')
    final_event = {
        'event_id': 'evt-parallel-final',
        'ordinal': 'ord-3',
        'type': 'final',
        'session_id': 'ses-parallel-tools',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-parallel-final',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-07-04T12:00:01Z',
            },
            'usage': {'input_tokens': 1, 'output_tokens': 1},
            'tool_calls': {'count': 2, 'names': ['first_tool', 'second_tool']},
        },
        'ts': '2026-07-04T12:00:01Z',
    }
    phase_event = _tool_phase_started_event('ses-parallel-tools', ordinal='ord-3')
    events.append(f'id: 3\nevent: system_tool_chunk\ndata: {json.dumps(phase_event)}\n\n')
    events.append(f'id: 4\nevent: final\ndata: {json.dumps(final_event)}\n\n')
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=''.join(events),
    )


def _coexistence_sse_response() -> httpx.Response:
    """Return two dependency-ordered app calls followed by a canonical follow-up."""
    events: list[str] = []
    for position, tool_name in enumerate(('inspect_service', 'prepare_deployment'), start=1):
        call_id = f'call-{tool_name}'
        event = {
            'event_id': f'evt-{tool_name}',
            'ordinal': f'ord-{position}',
            'type': 'system_tool_start',
            'session_id': 'ses-coexist',
            'root_event_id': 'evt-root',
            'correlation_id': call_id,
            'actor': f'tool:{tool_name}',
            'mode': 'hosted',
            'trigger_chain_depth': 0,
            'job_depth': 0,
            'job_index': 0,
            'payload': {
                'stage': 'tool_start',
                'tool_name': tool_name,
                'tool_call': {
                    'call_id': call_id,
                    'spec_ref': {
                        'tool_id': tool_name,
                        'namespace': 'sdk',
                        'version': 'v1',
                    },
                    'arguments': {},
                    'lineage': {
                        'session_id': 'ses-coexist',
                        'invocation_id': 'inv-coexist',
                    },
                },
            },
            'ts': '2026-08-04T12:00:00Z',
        }
        events.append(f'id: {position}\nevent: system_tool_start\ndata: {json.dumps(event)}\n\n')
    phase = _tool_phase_started_event('ses-coexist', ordinal='ord-3')
    events.append(f'id: 3\nevent: system_tool_chunk\ndata: {json.dumps(phase)}\n\n')
    tool_interrupt = {
        'event_id': 'evt-tool-argument-coexist',
        'ordinal': 'ord-4',
        'type': 'interrupt_required',
        'session_id': 'ses-coexist',
        'root_event_id': 'evt-root',
        'payload': {
            'interrupt': {
                'kind': 'tool_argument',
                'checkpoint_id': 'tool-argument-coexist',
                'thread_id': 'thr-coexist',
                'session_id': 'ses-coexist',
                'tool_name': 'prepare_deployment',
                'argument_name': 'ticket_id',
                'prompt_source': 'authored',
                'question': 'Deployment ticket: ',
                'response_schema': {'type': 'string'},
            }
        },
        'ts': '2026-08-04T12:00:01Z',
    }
    events.append(f'id: 4\nevent: interrupt_required\ndata: {json.dumps(tool_interrupt)}\n\n')
    resumed_call = {
        'event_id': 'evt-prepare-deployment-resumed',
        'ordinal': 'ord-5',
        'type': 'system_tool_start',
        'session_id': 'ses-coexist',
        'root_event_id': 'evt-root',
        'correlation_id': 'call-prepare_deployment',
        'actor': 'tool:prepare_deployment',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': {
            'stage': 'tool_start',
            'tool_name': 'prepare_deployment',
            'tool_call': {
                'call_id': 'call-prepare_deployment',
                'spec_ref': {
                    'tool_id': 'prepare_deployment',
                    'namespace': 'sdk',
                    'version': 'v1',
                },
                'arguments': {'service': 'billing-api', 'ticket_id': 'DEP-290'},
                'lineage': {
                    'session_id': 'ses-coexist',
                    'invocation_id': 'inv-coexist',
                },
            },
        },
        'ts': '2026-08-04T12:00:01Z',
    }
    events.append(f'id: 5\nevent: system_tool_start\ndata: {json.dumps(resumed_call)}\n\n')
    resumed_phase = _tool_phase_started_event('ses-coexist', ordinal='ord-6')
    events.append(f'id: 6\nevent: system_tool_chunk\ndata: {json.dumps(resumed_phase)}\n\n')
    interrupt = {
        'event_id': 'evt-followup-coexist',
        'ordinal': 'ord-7',
        'type': 'session_interrupt',
        'session_id': 'ses-coexist',
        'root_event_id': 'evt-root',
        'payload': {
            'interrupt': {
                'kind': 'agent_followup',
                'checkpoint_id': 'followup-coexist',
                'thread_id': 'thr-coexist',
                'session_id': 'ses-coexist',
                'header': 'Region',
                'question': 'Which region should I use?',
                'explanation': 'This determines residency.',
                'options': [
                    {'label': 'US East', 'description': 'Lowest latency.'},
                    {'label': 'EU West', 'description': 'EU residency.'},
                ],
                'multi_select': False,
                'required': True,
                'response_schema': {'type': 'string'},
            }
        },
        'ts': '2026-08-04T12:00:01Z',
    }
    events.append(f'id: 7\nevent: session_interrupt\ndata: {json.dumps(interrupt)}\n\n')
    events.append(_invoke_sse_response('ses-coexist', '{"answer":"typed"}').text)
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=''.join(events),
    )


def _tool_phase_started_event(session_id: str, *, ordinal: str) -> dict[str, object]:
    return {
        'event_id': f'evt-{session_id}-tool-phase',
        'ordinal': ordinal,
        'type': 'system_tool_chunk',
        'session_id': session_id,
        'root_event_id': 'evt-root',
        'actor': 'invoke',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': {'stage': 'tool_execution', 'phase': 'started'},
        'ts': '2026-07-04T12:00:00Z',
    }


def test_depends_on_agent_ships_the_agent_to_the_server_and_does_not_run_it() -> None:
    """The SDK declares the delegation on the wire; the SERVER performs it.

    The SDK used to invoke the sub-agent itself, which shipped our routing logic to
    customers in readable Python and left every non-SDK client unable to delegate at all.
    A second /v1/invoke here would mean the SDK is still doing the delegating.
    """
    bodies: list[dict[str, object]] = []
    invoke_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal invoke_calls
        if request.url.path == '/v1/invoke':
            invoke_calls += 1
            body = _json_body(request)
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-delegate', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-delegate', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    researcher = Agent(
        name='researcher',
        system_prompt='Research well.',
        client=client,
        included_nested_synthesis=False,
    )
    parent = Agent(name='parent-agent', client=client)

    @parent.toolify().depends_on_agent(researcher, 'research')
    def summarize(topic: str, research: str) -> str:
        return f'{topic}:{research}'

    _ = summarize
    _ = parent.invoke('summarize churn')

    assert invoke_calls == 1, 'the SDK must not invoke the sub-agent itself'
    body = bodies[0]
    assert body['agents'] == [
        {
            'name': 'researcher',
            'system_prompt': 'Research well.',
            'model': 'auto',
            'included_nested_synthesis': False,
        },
    ]
    assert body['agent_tool_dependencies'] == [
        {
            'tool_id': 'summarize',
            'agent_name': 'researcher',
            'arg_name': 'research',
            'optional': False,
        },
    ]


def test_depends_on_agent_ships_the_delegates_tool_specs_whole() -> None:
    """A delegate's own tools cross the wire as full specs, not just names.

    The server runs the delegate as a nested session, and tool ids alone name tools the
    server has never seen - which is exactly how a delegate once arrived with an empty
    tool list and failed every task that needed one.
    """
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-delegate-tools', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-delegate-tools', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    researcher = Agent(name='researcher', system_prompt='Research well.', client=client)

    @researcher.toolify(name='analyze_dataset', description='Analyze one dataset.')
    def analyze_dataset(dataset: str) -> dict[str, object]:
        return {'dataset': dataset}

    _ = analyze_dataset
    parent = Agent(name='parent-agent', client=client)

    @parent.toolify().depends_on_agent(researcher, 'research')
    def summarize(topic: str, research: str) -> str:
        return f'{topic}:{research}'

    _ = summarize
    _ = parent.invoke('summarize churn')

    agents = bodies[0]['agents']
    assert isinstance(agents, list)
    spec = cast('dict[str, object]', agents[0])
    assert spec['tool_ids'] == ['analyze_dataset']
    shipped_tools = cast('list[dict[str, object]]', spec['tools'])
    assert [tool['name'] for tool in shipped_tools] == ['analyze_dataset']
    assert shipped_tools[0]['namespace'] == 'sdk'
    assert isinstance(shipped_tools[0]['input_schema'], dict)


def test_swarm_invoke_sends_its_tools() -> None:
    """A swarm run must reach the model with its tools.

    Swarm.compile_tools() has always existed and its output was never sent, so every swarm
    run arrived at the model with ZERO tools. Both swarm demos passed the whole time - they
    exercise the call, not the payload.
    """
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-swarm-tools', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-swarm-tools', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    swarm = Swarm(name='research-swarm', client=client)

    @swarm.toolify()
    def lookup(topic: str) -> str:
        return topic

    _ = lookup
    _ = swarm.invoke('go')

    tools = cast('list[dict[str, object]]', bodies[0]['tools'])
    assert [tool['name'] for tool in tools] == ['lookup']


def test_agent_invoke_serializes_a_routing_preference_on_the_run_config() -> None:
    """A developer can ask automatic routing to lean cheaper without pinning a model.

    The preference has to reach the wire as its own field: pinning a tier
    instead would give up content routing entirely, which is the opposite of
    what "route this for me, but cheaply" asks for.
    """
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-preference', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-preference', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='preference-agent', client=client)

    _ = agent.invoke('hello', routing_preference='cost')

    first_call = calls[0]
    assert first_call is not None
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert run_config['routing_preference'] == 'cost'
    # Still automatic routing: the preference leans the choice, it does not make
    # it. Only 'auto' reads the preference at all, so this pairing is the whole
    # point - any other directive would have already named its own tier.
    assert run_config['model_directive'] == 'auto'


def test_agent_invoke_omits_the_routing_preference_when_none_is_stated() -> None:
    """No preference must serialize as absent, not as a default value."""
    calls: list[dict[str, object] | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(_json_body(request))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-no-preference', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-no-preference', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='plain-agent', client=client)

    _ = agent.invoke('hello')

    first_call = calls[0]
    assert first_call is not None
    run_config = cast('dict[str, object]', first_call['run_config'])
    assert 'routing_preference' not in run_config


def test_inline_member_preserves_explicit_system_tool_denial() -> None:
    """An inline roster must carry a member's empty built-in tool allowlist."""
    from maivn._internal.wire import (  # noqa: PLC0415
        _agent_spec,  # pyright: ignore[reportPrivateUsage]
    )

    member = Agent(name='restricted', api_key='test-key', system_tools_config={'allowed_tools': []})
    spec = _agent_spec(member, member.name)
    assert spec['system_tools_config'] == {'allowed_system_tools': []}
