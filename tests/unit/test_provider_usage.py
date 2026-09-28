"""Unit tests for the provider-reported usage surface on runs and sessions."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, TypeAlias

import httpx
from maivn_contracts.messages import Message
from pydantic import AnyUrl

import maivn
from maivn import Agent, Client, ClientConfig, InvokeResponse, ProviderModelUsage, ProviderUsage

JsonObject: TypeAlias = dict[str, Any]

_SESSION_ID = 'ses-usage'
_RUN_INPUT_TOKENS = 5391
_SESSION_INPUT_TOKENS = 6100
_SESSION_CALLS = 6

_PROVIDER_BLOCK: JsonObject = {
    'scope': 'run',
    'settled': False,
    'provider_counts_complete': True,
    'calls': 4,
    'input_tokens': _RUN_INPUT_TOKENS,
    'output_tokens': 812,
    'cache_read_input_tokens': 0,
    'cache_creation_input_tokens': 1200,
    'by_model': [
        {
            'provider': 'provider-a',
            'model': 'model-alpha',
            'calls': 3,
            'input_tokens': 5100,
            'output_tokens': 700,
            'cache_read_input_tokens': 0,
            'cache_creation_input_tokens': 1200,
        },
    ],
}


def _final_message() -> Message:
    return Message.model_validate(
        {
            'message_id': 'msg-final',
            'role': 'assistant',
            'content': 'done',
            'ts': '2026-09-13T12:00:00Z',
        },
    )


def _response(usage: JsonObject) -> InvokeResponse:
    return InvokeResponse(
        final_message=_final_message(),
        session_id=_SESSION_ID,
        root_event_id='evt-root',
        event_positions=[1],
        usage=usage,
        response='done',
    )


def test_provider_usage_parses_the_block_the_platform_published() -> None:
    """The run's provider counts come back typed, per provider and model."""
    provider_usage = _response(
        {'input_tokens': 1477, 'output_tokens': 812, 'provider': _PROVIDER_BLOCK},
    ).provider_usage

    assert provider_usage == ProviderUsage(
        scope='run',
        settled=False,
        provider_counts_complete=True,
        calls=4,
        input_tokens=_RUN_INPUT_TOKENS,
        output_tokens=812,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=1200,
        by_model=(
            ProviderModelUsage(
                provider='provider-a',
                model='model-alpha',
                calls=3,
                input_tokens=5100,
                output_tokens=700,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=1200,
            ),
        ),
    )


def test_a_response_without_the_block_reports_nothing_rather_than_zero() -> None:
    """An older platform publishes no provider counts, and that is not a free run."""
    assert _response({'input_tokens': 1477, 'output_tokens': 812}).provider_usage is None
    assert _response(
        {'input_tokens': 1, 'output_tokens': 1, 'provider': 'nope'}
    ).provider_usage is (None)


def test_the_public_usage_keys_are_untouched_by_the_provider_block() -> None:
    """Reading provider counts must not change what the platform's own counts say."""
    response = _response({'input_tokens': 1477, 'output_tokens': 812, 'provider': _PROVIDER_BLOCK})

    assert response.usage['input_tokens'] == 1477  # noqa: PLR2004 - the platform's own count.
    token_usage = response.token_usage
    assert token_usage is not None
    assert token_usage['output_tokens'] == 812  # noqa: PLR2004 - the platform's own count.


def _session_usage_client(calls: list[tuple[str, str, str | None]]) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get('authorization')))
        return httpx.Response(
            HTTPStatus.OK,
            json={
                'session_id': _SESSION_ID,
                'scope': 'session',
                'settled': True,
                'as_of': '2026-09-13T12:00:00Z',
                'provider_counts_complete': True,
                'calls': _SESSION_CALLS,
                'input_tokens': _SESSION_INPUT_TOKENS,
                'output_tokens': 900,
                'cache_read_input_tokens': 0,
                'cache_creation_input_tokens': 1200,
                'by_model': [],
            },
        )

    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )


def test_agent_session_usage_reads_the_session_route_with_the_api_key() -> None:
    """The agent asks the session endpoint and returns the typed total."""
    calls: list[tuple[str, str, str | None]] = []
    agent = Agent(name='usage', client=_session_usage_client(calls))

    usage = agent.session_usage(_SESSION_ID)

    assert calls == [('GET', f'/v1/sessions/{_SESSION_ID}/usage', 'Bearer test-key')]
    assert usage.scope == 'session'
    assert usage.settled is True
    assert usage.calls == _SESSION_CALLS
    assert usage.input_tokens == _SESSION_INPUT_TOKENS
    assert usage.as_of is not None


def test_the_public_namespace_exports_both_usage_models() -> None:
    """`from maivn import ProviderUsage, ProviderModelUsage` is the documented import."""
    assert maivn.ProviderUsage is ProviderUsage
    assert maivn.ProviderModelUsage is ProviderModelUsage
    assert {'ProviderUsage', 'ProviderModelUsage'} <= set(maivn.__all__)
