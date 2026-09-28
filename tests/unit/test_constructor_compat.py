"""Regression tests for v1-compatible Agent and Client constructors."""

from __future__ import annotations

import json
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, TypeAlias, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig

JsonObject: TypeAlias = dict[str, Any]
Handler: TypeAlias = Callable[[httpx.Request], httpx.Response]
AGENT_TIMEOUT = 17.0
AGENT_MAX_RESULTS = 7
CLIENT_TIMEOUT = 11.0
TOOL_TIMEOUT = 12.0
DEPENDENCY_TIMEOUT = 13.0
TOTAL_TIMEOUT = 14.0


def test_agent_accepts_all_v1_constructor_options() -> None:
    """Agent accepts all restored v1 constructor fields together."""
    client = _client()

    agent = Agent(
        name='compat-agent',
        client=client,
        timeout=AGENT_TIMEOUT,
        max_results=AGENT_MAX_RESULTS,
        force_final_tool=True,
    )

    assert agent.timeout == AGENT_TIMEOUT
    assert agent.max_results == AGENT_MAX_RESULTS
    assert agent.force_final_tool is True
    assert agent.included_nested_synthesis == 'auto'


@pytest.mark.parametrize(
    ('kwargs', 'attribute', 'expected'),
    [
        ({'timeout': 11.0}, 'timeout', 11.0),
        ({'max_results': 3}, 'max_results', 3),
        ({'force_final_tool': True}, 'force_final_tool', True),
        ({'included_nested_synthesis': False}, 'included_nested_synthesis', False),
    ],
)
def test_agent_accepts_each_v1_constructor_option_individually(
    kwargs: dict[str, object],
    attribute: str,
    expected: object,
) -> None:
    """Agent accepts each restored v1 constructor field on its own."""
    agent = Agent(name='compat-agent', client=_client(), **cast('dict[str, Any]', kwargs))

    assert getattr(agent, attribute) == expected


def test_agent_requires_client_or_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent preserves the v1 missing-credential exception."""
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)

    with pytest.raises(ValueError, match='Agent requires either a Client instance or an api_key'):
        Agent(name='missing-credentials-agent')


def test_agent_constructor_force_final_tool_is_overridden_by_call() -> None:
    """An explicit call-time force-final value overrides the constructor default."""
    bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-compat', 'stream_position': 0},
            )
        return _sse_response()

    client = _client(handler)
    agent = Agent(name='compat-agent', client=client, force_final_tool=True)

    @agent.toolify(final_tool=True)
    def finish() -> str:
        return 'finished'

    assert callable(finish)
    _ = agent.invoke('first', force_final_tool=False)
    _ = agent.invoke('second')

    assert 'force_final_tool' not in bodies[0]
    assert bodies[1]['force_final_tool'] is True


def test_client_accepts_v1_constructor_options_and_applies_defaults() -> None:
    """Client accepts and stores all restored v1 constructor fields together."""
    client = Client(
        api_key='test-key',
        base_url='http://testserver',
        timeout=CLIENT_TIMEOUT,
        thread_id='thr-default',
        tool_execution_timeout=TOOL_TIMEOUT,
        dependency_wait_timeout=DEPENDENCY_TIMEOUT,
        total_execution_timeout=TOTAL_TIMEOUT,
    )

    assert client.config.timeout_seconds == CLIENT_TIMEOUT
    assert client.config.thread_id == 'thr-default'
    assert client.config.tool_execution_timeout == TOOL_TIMEOUT
    assert client.config.dependency_wait_timeout == DEPENDENCY_TIMEOUT
    assert client.config.total_execution_timeout == TOTAL_TIMEOUT


@pytest.mark.parametrize(
    ('kwargs', 'attribute', 'expected'),
    [
        ({'timeout': 5.0}, 'timeout_seconds', 5.0),
        ({'thread_id': 'thr-one'}, 'thread_id', 'thr-one'),
        ({'tool_execution_timeout': 6.0}, 'tool_execution_timeout', 6.0),
        ({'dependency_wait_timeout': 7.0}, 'dependency_wait_timeout', 7.0),
        ({'total_execution_timeout': 8.0}, 'total_execution_timeout', 8.0),
    ],
)
def test_client_accepts_each_v1_constructor_option_individually(
    kwargs: dict[str, object],
    attribute: str,
    expected: object,
) -> None:
    """Client accepts each restored v1 constructor field on its own."""
    client = Client(
        api_key='test-key',
        base_url='http://testserver',
        **cast('dict[str, Any]', kwargs),
    )

    assert getattr(client.config, attribute) == expected


def test_client_timeout_seconds_wins_when_aliases_match() -> None:
    """The v2 timeout spelling wins when both aliases carry the same value."""
    client = Client(
        api_key='test-key',
        base_url='http://testserver',
        timeout=CLIENT_TIMEOUT,
        timeout_seconds=CLIENT_TIMEOUT,
    )

    assert client.config.timeout_seconds == CLIENT_TIMEOUT


def test_client_rejects_conflicting_timeout_aliases() -> None:
    """Conflicting v1 and v2 timeout aliases fail clearly."""
    with pytest.raises(ValueError, match='timeout and timeout_seconds'):
        Client(
            api_key='test-key',
            base_url='http://testserver',
            timeout=5.0,
            timeout_seconds=6.0,
        )


def test_client_thread_id_is_default_for_agent_invoke() -> None:
    """Client thread_id flows into an Agent invoke that omits a call thread."""
    bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-thread-default', 'stream_position': 0},
            )
        return _sse_response(session_id='ses-thread-default')

    client = _client(handler, thread_id='thr-default')
    Agent(name='compat-agent', client=client).invoke('hello')

    assert bodies[0]['run_config']['thread_id'] == 'thr-default'


def _client(
    handler: Handler | None = None,
    *,
    thread_id: str | None = None,
) -> Client:
    transport = httpx.MockTransport(handler or (lambda _request: _sse_response()))
    return Client(
        config=ClientConfig(
            api_key='test-key',
            base_url=AnyUrl('http://testserver'),
            thread_id=thread_id,
        ),
        transport=transport,
    )


def _json_body(request: httpx.Request) -> JsonObject | None:
    payload = request.read()
    if not payload:
        return None
    return cast('JsonObject', json.loads(payload))


def _sse_response(*, session_id: str = 'ses-compat') -> httpx.Response:
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=(
            'id: 1\nevent: final\n'
            'data: {"event_id":"evt-final","ordinal":"ord-final",'
            f'"type":"final","session_id":"{session_id}",'
            '"root_event_id":"evt-root","payload":{"message":{"message_id":"msg-final",'
            '"role":"assistant","content":"done","ts":"2026-07-04T12:00:00Z"},'
            '"tool_calls":{"count":0,"names":[]}},"ts":"2026-07-04T12:00:00Z"}\n\n'
        ),
    )
