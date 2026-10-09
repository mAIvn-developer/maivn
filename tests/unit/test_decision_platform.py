"""Decisions go through the mAIvn platform: wire contract, receipts and failure routing."""

# ruff: noqa: D103, PLR2004 - behavioural tests named for what they prove.

from __future__ import annotations

import asyncio
import inspect
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionRequest,
    DecisionStatus,
)
from tests.unit.decision_platform import API_KEY, PLATFORM_MODEL, OfflinePlatform, choosing


def _request(name: str = 'route', state: str = 'public example') -> DecisionRequest:
    return DecisionRequest(
        name=name,
        state=state,
        instructions='Choose',
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
    )


async def _baseline(request: DecisionRequest) -> DecisionAnswer:
    return DecisionAnswer(value=request.choices[0].key, provider='local', token_category=None)


def _client(platform: OfflinePlatform, *, allow: bool = True, **config: object) -> DecisionClient:
    return DecisionClient(
        fallback=_baseline,
        config=DecisionConfig.model_validate(config),
        authorize=lambda _request: allow,
        client=platform.client(),
    )


def test_one_decision_posts_only_public_content_with_the_maivn_key() -> None:
    platform = choosing(lambda _q, _s: ('no', 0.95), usage={'input_tokens': 12, 'output_tokens': 1})
    result = _client(platform).decide(_request())

    assert result.value == 'no'
    assert result.source == 'primary'
    assert result.primary.status == DecisionStatus.ACCEPTED
    assert result.primary.provider == 'maivn'
    assert result.primary.model == PLATFORM_MODEL
    assert result.primary.usage is not None
    assert result.primary.usage.input_tokens == 12
    (body,) = platform.calls
    # No model name, decision-model key or request label crosses the wire.
    assert body == {
        'state': 'public example',
        'questions': [
            {
                'name': 'q0',
                'instructions': 'Choose',
                'choices': [
                    {'key': 'yes', 'description': 'yes'},
                    {'key': 'no', 'description': 'no'},
                ],
            }
        ],
    }
    (sent,) = platform.requests
    assert sent.headers['authorization'] == f'Bearer {API_KEY}'
    assert sent.url.host == 'api.example.test'


def test_native_batch_shares_one_call_and_one_receipt_owner() -> None:
    platform = choosing(lambda _q, _s: ('yes', 0.9), usage={'input_tokens': 30})
    client = _client(platform, max_batch_questions=4)
    requests = [_request(name) for name in ('a', 'b', 'c')]
    results = client.batch(requests, native_batch=True)

    assert len(platform.calls) == 1
    assert [q['name'] for q in platform.calls[0]['questions']] == ['q0', 'q1', 'q2']
    assert [result.value for result in results] == ['yes', 'yes', 'yes']
    assert [result.primary.usage_owner for result in results] == [True, False, False]
    assert results[0].primary.usage is not None
    assert all(result.primary.usage is None for result in results[1:])
    assert client.native_batch_question_limit == 4


@pytest.mark.parametrize(
    ('reply', 'status'),
    [
        (httpx.Response(200, json={'status': 'refused'}), DecisionStatus.BLOCKED),
        (httpx.Response(200, json={'status': 'unavailable'}), DecisionStatus.UNAVAILABLE),
        (httpx.Response(200, json={'status': 'decided', 'answers': []}), DecisionStatus.INVALID),
        (httpx.Response(200, json=['not', 'an', 'object']), DecisionStatus.UNAVAILABLE),
        (httpx.Response(503, json={'detail': 'down', 'code': 'x'}), DecisionStatus.UNAVAILABLE),
        (
            httpx.Response(
                429,
                json={'detail': 'Plan limit reached', 'code': 'plan_limit_exceeded'},
            ),
            DecisionStatus.UNAVAILABLE,
        ),
    ],
)
def test_platform_outcomes_route_to_the_baseline(
    reply: httpx.Response, status: DecisionStatus
) -> None:
    platform = OfflinePlatform(lambda _body: reply)
    result = _client(platform).decide(_request())

    assert result.source == 'fallback'
    assert result.value == 'yes'
    assert result.primary.status == status


def test_refused_and_invalid_answers_are_per_question() -> None:
    def choose(question: dict[str, object], _state: str) -> tuple[str, float] | str:
        return {'q0': 'refused', 'q1': 'invalid'}.get(str(question['name']), ('no', 0.99))

    platform = choosing(choose)
    results = _client(platform).batch(
        [_request('a'), _request('b'), _request('c')], native_batch=True
    )

    assert [result.primary.status for result in results] == [
        DecisionStatus.BLOCKED,
        DecisionStatus.INVALID,
        DecisionStatus.ACCEPTED,
    ]
    assert [result.source for result in results] == ['fallback', 'fallback', 'primary']


def test_undeclared_value_and_low_confidence_fall_back() -> None:
    platform = choosing(lambda _q, _s: ('maybe', 0.99))
    assert _client(platform).decide(_request()).primary.status == DecisionStatus.INVALID
    platform = choosing(lambda _q, _s: ('no', 0.2))
    assert _client(platform).decide(_request()).primary.status == DecisionStatus.LOW_CONFIDENCE


@pytest.mark.parametrize('privacy', ['private', 'unknown'])
def test_nonpublic_or_unapproved_requests_never_reach_the_platform(privacy: str) -> None:
    platform = choosing(lambda _q, _s: ('no', 0.99))
    request = _request().model_copy(update={'privacy': privacy})
    assert _client(platform).decide(request).primary.status == DecisionStatus.BLOCKED
    assert _client(platform, allow=False).decide(_request()).primary.status == (
        DecisionStatus.BLOCKED
    )
    assert platform.calls == []


def test_authorization_failure_reads_no_client() -> None:
    def explode(_request: DecisionRequest) -> bool:
        message = 'local policy unavailable'
        raise RuntimeError(message)

    # No client and no environment: approval refuses before a client would be built.
    client = DecisionClient(fallback=_baseline, config=DecisionConfig(), authorize=explode)
    assert client.decide(_request()).primary.status == DecisionStatus.BLOCKED


def test_disabled_config_makes_no_call() -> None:
    platform = choosing(lambda _q, _s: ('no', 0.99))
    result = _client(platform, enabled=False).decide(_request())
    assert result.primary.status == DecisionStatus.DISABLED
    assert platform.calls == []


def test_timeout_routes_to_the_baseline() -> None:
    gate = asyncio.Event()

    async def slow(_body: dict[str, object]) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json={'status': 'unavailable'})

    result = _client(OfflinePlatform(slow), timeout_seconds=0.05).decide(_request())
    assert result.primary.status == DecisionStatus.TIMEOUT
    assert result.source == 'fallback'


def test_public_api_has_no_model_name_or_decision_model_key() -> None:
    from maivn import decisions  # noqa: PLC0415 - read the public module as a caller would.

    assert not hasattr(decisions, 'JevDecisionProvider')
    assert not hasattr(decisions, 'DecisionProvider')
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate({'model': 'any-model'})
    parameters = inspect.signature(DecisionClient).parameters
    assert set(parameters) == {'fallback', 'config', 'authorize', 'client'}


def _marked(name: str = 'owner', **private: str) -> DecisionRequest:
    return DecisionRequest(
        name=name,
        state='Priya Raman reports the billing page fails to load.',
        instructions='Who owns the follow-up?',
        privacy='public',
        private_data=cast('Any', private or {'customer_name': 'Priya Raman'}),
        choices=(
            DecisionChoice(key='Priya Raman', description='The customer'),
            DecisionChoice(key='support', description='The support team'),
        ),
    )


def test_marked_private_values_go_to_the_platform_shield_and_placeholders_resolve() -> None:
    # The platform answers with the shielded key; the SDK resolves it locally.
    platform = choosing(lambda _q, _s: ('{_{customer_name}_}', 0.9))
    result = _client(platform).decide(_marked())

    assert result.source == 'primary'
    assert result.value == 'Priya Raman'
    (body,) = platform.calls
    assert body['private_data'] == {'customer_name': 'Priya Raman'}


def test_requests_with_different_marked_values_never_share_a_call() -> None:
    platform = choosing(lambda _q, _s: ('support', 0.9))
    requests = [_marked('a'), _marked('b', customer_name='Someone Else')]
    results = _client(platform).batch(requests, native_batch=True)

    assert [result.value for result in results] == ['support', 'support']
    assert len(platform.calls) == 2


def test_unmarked_requests_send_no_private_field() -> None:
    platform = choosing(lambda _q, _s: ('no', 0.9))
    _client(platform).decide(_request())
    assert 'private_data' not in platform.calls[0]
