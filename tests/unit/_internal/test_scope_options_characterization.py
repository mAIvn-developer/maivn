"""Characterization tests for per-call run-option resolution on the invoke wire."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import (
    Agent,
    Client,
    ClientConfig,
    ModelChoice,
    ModelConfig,
    SessionOrchestrationConfig,
    SystemToolsConfig,
)
from maivn.messages import HumanMessage, SystemMessage

JsonObject = dict[str, Any]


def _final_sse() -> httpx.Response:
    event: JsonObject = {
        'event_id': 'evt-final',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': 'ses-opt',
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


def _invoke_body(agent_kwargs: JsonObject, messages: object, **invoke_kwargs: Any) -> JsonObject:
    bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            bodies.append(cast('JsonObject', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-opt', 'stream_position': 0}
            )
        return _final_sse()

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    agent = Agent(name='opts', client=client, **agent_kwargs)
    _ = agent.invoke(cast('Any', messages), thread_id='thr-opts', **invoke_kwargs)
    (body,) = bodies
    for message in body['messages']:
        message.pop('message_id')
        message.pop('ts')
    return body


@pytest.mark.parametrize(
    ('model', 'expected_config', 'expected_model'),
    [
        ('  balanced ', {'model_directive': 'balanced'}, 'auto'),
        ('vendor/model-x', {'model_directive': 'force'}, 'vendor/model-x'),
        (ModelChoice(tier='max'), {'model_directive': 'max'}, 'auto'),
        (ModelChoice(model_id='vendor/exact'), {'model_directive': 'force'}, 'vendor/exact'),
        (
            ModelConfig(
                thinking=ModelChoice(tier='fast'), repl=ModelChoice(model_id='vendor/repl')
            ),
            {
                'model_directive': 'auto',
                'system_model_choices': {
                    'thinking': {'tier': 'fast'},
                    'repl': {'model_id': 'vendor/repl'},
                },
            },
            'auto',
        ),
    ],
)
def test_model_overrides_resolve_to_directives_and_models(
    model: object,
    expected_config: JsonObject,
    expected_model: str,
) -> None:
    """String, ModelChoice, and ModelConfig overrides map onto directive and model fields."""
    body = _invoke_body({}, 'hello', model=model)

    assert body['run_config'] == {
        'user_id': 'sdk-user',
        'thread_id': 'thr-opts',
        'agent_id': 'opts',
        **({'model_directive': 'auto'} | expected_config),
    }
    assert body['model'] == expected_model


@pytest.mark.parametrize(
    ('model', 'error', 'message'),
    [
        ('   ', ValueError, 'model override must not be blank'),
        (42, TypeError, 'unsupported model override type: int'),
    ],
)
def test_invalid_model_overrides_are_rejected(
    model: object, error: type[Exception], message: str
) -> None:
    """Blank strings and unsupported types are refused with exact messages."""
    with pytest.raises(error) as raised:
        _ = _invoke_body({}, 'hello', model=model)

    assert str(raised.value) == message


def test_empty_config_overrides_add_no_wire_fields() -> None:
    """Config overrides that produce no patch leave the invoke body untouched."""
    body = _invoke_body(
        {},
        'hello',
        system_tools_config=SystemToolsConfig(),
        orchestration_config=SessionOrchestrationConfig(),
    )

    assert 'system_tools_config' not in body
    assert 'orchestration_config' not in body
    assert body['run_config'] == {
        'user_id': 'sdk-user',
        'thread_id': 'thr-opts',
        'agent_id': 'opts',
        'model_directive': 'auto',
    }


def test_scope_system_prompt_is_prepended_only_without_an_existing_system_message() -> None:
    """A caller-supplied system message wins over the scope system prompt."""
    prompted = _invoke_body({'system_prompt': SystemMessage(content='Scope prompt.')}, 'hello')
    caller_owned = _invoke_body(
        {'system_prompt': 'Scope prompt.'},
        [SystemMessage(content='Caller prompt.'), HumanMessage(content='hello')],
    )

    assert prompted['messages'] == [
        {'role': 'system', 'content': 'Scope prompt.'},
        {'role': 'user', 'content': 'hello'},
    ]
    assert caller_owned['messages'] == [
        {'role': 'system', 'content': 'Caller prompt.'},
        {'role': 'user', 'content': 'hello'},
    ]
