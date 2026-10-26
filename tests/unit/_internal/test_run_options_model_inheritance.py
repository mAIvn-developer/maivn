"""Per-call RunOptions keep the agent's model unless the caller chose one (U05-D13)."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, RunOptions

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


def _sent_model(agent_model: str, options: RunOptions) -> tuple[str, object]:
    """Return the invoke request's ``model`` and ``run_config.model_directive``."""
    bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            bodies.append(cast('JsonObject', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-opt', 'stream_position': 0},
            )
        return _final_sse()

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    Agent(name='opts', client=client, model=agent_model).invoke('hello', options=options)
    (body,) = bodies
    return body['model'], body['run_config'].get('model_directive')


@pytest.mark.parametrize(
    ('agent_model', 'expected'),
    [
        ('fast', ('auto', 'fast')),
        ('vendor/exact-model', ('vendor/exact-model', None)),
    ],
)
def test_options_without_a_model_keep_the_agent_model(
    agent_model: str,
    expected: tuple[str, object],
) -> None:
    """Options set only for a thread inherit the agent's tier or exact model id."""
    assert _sent_model(agent_model, RunOptions(thread_id='thr-1')) == expected


def test_an_explicit_auto_model_overrides_the_agent_tier() -> None:
    """A caller who writes model='auto' gets automatic routing, not the agent's tier."""
    assert _sent_model('fast', RunOptions(model='auto')) == ('auto', None)


def test_an_explicit_directive_overrides_the_agent_model() -> None:
    """A caller-set directive wins over an agent's exact model id."""
    assert _sent_model('vendor/exact-model', RunOptions(model_directive='max')) == (
        'auto',
        'max',
    )


def test_an_explicit_exact_model_overrides_the_agent_tier() -> None:
    """A caller-set exact model id is sent unchanged, exactly as the caller's options say."""
    assert _sent_model('fast', RunOptions(model='vendor/other-model')) == (
        'vendor/other-model',
        None,
    )
