"""Characterization tests for the JSON the SDK sends for tools, delegates, and run config."""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import AnyUrl, BaseModel

from maivn import (
    Agent,
    Client,
    ClientConfig,
    RunOptions,
    compose_argument_policy,
    depends_on_agent,
    depends_on_interrupt,
    depends_on_reevaluate,
    depends_on_tool,
)
from maivn._internal.wire import (
    _function_tool_spec,  # pyright: ignore[reportPrivateUsage]
    run_config_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable

JsonObject = dict[str, Any]
_TIMEOUT_MS = 600_000


def _final_sse() -> httpx.Response:
    event: JsonObject = {
        'event_id': 'evt-final',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': 'ses-wire',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-final',
                'role': 'assistant',
                'content': 'done',
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


def _recording_client(bodies: list[JsonObject]) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            bodies.append(cast('JsonObject', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-wire', 'stream_position': 0}
            )
        return _final_sse()

    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )


def _spec(name: str, input_schema: JsonObject, **extra: object) -> JsonObject:
    return {
        'tool_id': name,
        'namespace': 'sdk',
        'version': 'v1',
        'idempotency': 'unknown',
        'timeout_ms': _TIMEOUT_MS,
        'kind': 'function',
        'name': name,
        'description': f'Execute {name}.',
        'input_schema': input_schema,
        **extra,
    }


class _Brief(BaseModel):
    topic: str
    research: str = 'none'
    review: str


def test_shared_delegate_serializes_once_with_one_edge_per_consumer() -> None:
    """Two tools that need the same delegate ship it once and list both edges."""
    bodies: list[JsonObject] = []
    agent = Agent(name='lead', client=_recording_client(bodies))
    researcher = Agent(name='researcher', api_key='test-key', base_url='http://testserver')
    reviewer = Agent(name='reviewer', api_key='test-key', base_url='http://testserver')

    def summarize(notes: str) -> str:
        return notes

    def critique(notes: str = '') -> str:
        return notes

    _ = agent.add_tool(depends_on_agent(researcher, 'notes')(summarize), name='summarize')
    _ = agent.add_tool(depends_on_agent(researcher, 'notes')(critique), name='critique')
    _ = agent.add_tool(
        depends_on_agent(reviewer, 'review')(depends_on_agent(researcher, 'research')(_Brief)),
        name='brief',
    )

    _ = agent.invoke('go')

    body = bodies[0]
    assert body['agents'] == [
        {'name': 'researcher', 'model': 'auto'},
        {'name': 'reviewer', 'model': 'auto'},
    ]
    assert body['agent_tool_dependencies'] == [
        {
            'tool_id': 'summarize',
            'agent_name': 'researcher',
            'arg_name': 'notes',
            'optional': False,
        },
        {'tool_id': 'critique', 'agent_name': 'researcher', 'arg_name': 'notes', 'optional': True},
        {'tool_id': 'brief', 'agent_name': 'researcher', 'arg_name': 'research', 'optional': True},
        {'tool_id': 'brief', 'agent_name': 'reviewer', 'arg_name': 'review', 'optional': False},
    ]


def test_interrupt_dependencies_serialize_prompt_source_and_schema() -> None:
    """Fallback prompts, choices, and unannotated arguments shape interrupt specs."""
    agent = Agent(name='asker', api_key='test-key', base_url='http://testserver')

    def handler(prompt: str) -> str:
        return prompt

    def choose(color, size: str) -> str:  # noqa: ANN001 - unannotated on purpose.  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
        return f'{color}{size}'

    def confirm(approved: bool) -> bool:  # noqa: FBT001 - interrupt-owned flag.
        return approved

    _ = agent.add_tool(
        depends_on_interrupt('size', handler, choices=['1', '2'])(
            depends_on_interrupt('color', handler)(cast('Callable[..., str]', choose)),
        ),
        name='choose',
    )
    _ = agent.add_tool(
        depends_on_interrupt('approved', handler, prompt='Proceed?')(confirm), name='confirm'
    )

    specs = {tool.name: _function_tool_spec(tool) for tool in agent.compile_tools()}

    assert specs['choose']['interrupt_dependencies'] == [
        {
            'arg_name': 'color',
            'prompt_source': 'fallback',
            'question': 'color: ',
            'response_schema': {'type': 'string'},
        },
        {
            'arg_name': 'size',
            'prompt_source': 'fallback',
            'question': 'size: ',
            'response_schema': {'type': 'string', 'enum': ['1', '2']},
        },
    ]
    assert specs['confirm']['interrupt_dependencies'] == [
        {
            'arg_name': 'approved',
            'prompt_source': 'authored',
            'question': 'Proceed?',
            'response_schema': {'type': 'boolean'},
        },
    ]


def test_compose_policy_on_a_hidden_dependency_argument_is_rejected() -> None:
    """A composition policy must target an argument the model can see."""
    agent = Agent(name='composer', api_key='test-key', base_url='http://testserver')

    def source() -> str:
        return 'value'

    def consume(value: str, note: str) -> str:
        return value + note

    _ = agent.add_tool(source, name='source')
    _ = agent.add_tool(
        compose_argument_policy('value', mode='require')(depends_on_tool(source, 'value')(consume)),
        name='consume',
    )
    consume_tool = next(tool for tool in agent.compile_tools() if tool.name == 'consume')

    with pytest.raises(
        TypeError, match=r"^compose_argument target 'value' has no argument schema$"
    ):
        _ = _function_tool_spec(consume_tool)


def test_malformed_reevaluate_metadata_is_skipped_and_valid_controls_are_kept() -> None:
    """Only well-formed reevaluate controls reach the invoke body."""
    bodies: list[JsonObject] = []
    agent = Agent(name='replanner', client=_recording_client(bodies))

    def anchor() -> str:
        return 'anchor'

    def not_a_list() -> str:
        return 'x'

    def mixed() -> str:
        return 'y'

    _ = agent.add_tool(anchor, name='anchor')
    _ = agent.add_tool(
        not_a_list, name='not_a_list', metadata={'execution_controls': {'reevaluate': 'anchor'}}
    )
    _ = agent.add_tool(
        mixed,
        name='mixed',
        metadata={
            'execution_controls': {
                'reevaluate': [
                    'anchor',
                    {'tool_id': 'anchor', 'tool_name': 'anchor', 'timing': 'after'},
                    {
                        'tool_id': 'anchor',
                        'tool_name': 'anchor',
                        'timing': 'before',
                        'instance_control': 'all',
                    },
                ],
            },
        },
    )
    _ = agent.add_tool(depends_on_reevaluate(anchor, timing='after')(lambda: 'z'), name='decorated')

    _ = agent.invoke('go')

    assert bodies[0]['reevaluate_controls'] == [
        {
            'kind': 'reevaluate',
            'dependent_tool_id': 'mixed',
            'dependent_tool_name': 'mixed',
            'tool_id': 'anchor',
            'tool_name': 'anchor',
            'timing': 'before',
            'instance_control': 'all',
        },
        {
            'kind': 'reevaluate',
            'dependent_tool_id': 'decorated',
            'dependent_tool_name': 'decorated',
            'tool_id': 'anchor',
            'tool_name': 'anchor',
            'timing': 'after',
            'instance_control': 'each',
        },
    ]
    assert bodies[0]['tools'][0] == _spec(
        'anchor', {'type': 'object', 'properties': {}, 'additionalProperties': False}
    )


def test_run_config_generates_a_thread_id_and_omits_unset_fields() -> None:
    """A run without a thread gets a fresh thr- id; unset optional fields are absent."""
    first = run_config_payload(RunOptions())
    second = run_config_payload(RunOptions())

    assert set(first) == {'user_id', 'thread_id'}
    assert first['user_id'] == 'sdk-user'
    assert re.fullmatch(r'thr-[0-9a-f]{32}', str(first['thread_id']))
    assert first['thread_id'] != second['thread_id']
