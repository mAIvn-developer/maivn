# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Exercise standalone decisions without contacting a provider."""

from __future__ import annotations

import asyncio
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from maivn._internal.decisions.platform import PlatformDecisions
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
    DecisionUsage,
)
from tests.unit.decision_platform import PLATFORM_MODEL, OfflinePlatform, decided


@pytest.fixture
def decision_request() -> DecisionRequest:
    """Return a fully public categorical decision_request."""
    return DecisionRequest(
        name='route',
        state='public example',
        instructions='Choose',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
        privacy='public',
    )


class StubProvider:
    """Controlled primary with observable attempts."""

    def __init__(self, outcome: DecisionPrediction | Exception | None = None) -> None:
        """Set the controlled primary outcome."""
        self.calls = 0
        self.outcome = outcome or DecisionPrediction(
            value='no', confidence=0.95, provider='fake', model='test'
        )

    async def predict(self, decision_request: DecisionRequest) -> DecisionPrediction:
        """Return the configured outcome."""
        assert decision_request.name
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def baseline(decision_request: DecisionRequest) -> DecisionAnswer:
    """Return a local baseline answer."""
    assert decision_request.name
    return DecisionAnswer(value='yes', provider='local')


def test_negative_and_repeated_sync(decision_request: DecisionRequest) -> None:
    """A confident negative is accepted and sync calls own separate loops."""
    client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        StubProvider(), fallback=baseline, config=DecisionConfig()
    )
    for _ in range(2):
        result = client.decide(decision_request)
        assert result.value == 'no'
        assert result.source == 'primary'
        assert result.primary.usage is None
    assert len(client.batch([decision_request, decision_request], max_concurrency=1)) == len((0, 1))


@pytest.mark.parametrize('privacy', ['private', 'unknown'])
def test_privacy_precedes_provider(decision_request: DecisionRequest, privacy: str) -> None:
    """Nonpublic inputs cannot reach even a custom provider."""
    provider = StubProvider()
    client = DecisionClient._from_backend(provider, fallback=baseline, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
    result = client.decide(decision_request.model_copy(update={'privacy': privacy}))
    assert result.primary.status == 'blocked'
    assert result.source == 'fallback'
    assert provider.calls == 0


@pytest.mark.parametrize(
    ('enabled', 'outcome', 'status'),
    [
        (False, None, 'disabled'),
        (True, RuntimeError('sensitive'), 'unavailable'),
        (
            True,
            DecisionPrediction(
                value='no',
                provider='fake',
                model='test',
                confidence=0.1,
                usage=DecisionUsage(input_tokens=8),
            ),
            'low_confidence',
        ),
        (
            True,
            DecisionPrediction(
                value='other',
                provider='fake',
                model='other',
                confidence=0.9,
                usage=DecisionUsage(output_tokens=4),
            ),
            'invalid',
        ),
    ],
)
def test_fallback_once(
    decision_request: DecisionRequest,
    *,
    enabled: bool,
    outcome: DecisionPrediction | Exception | None,
    status: str,
) -> None:
    """Every failed primary calls the baseline once and keeps known accounting."""
    calls = 0

    async def counted(inner: DecisionRequest) -> DecisionAnswer:
        nonlocal calls
        calls += 1
        return await baseline(inner)

    client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        StubProvider(outcome),
        fallback=counted,
        config=DecisionConfig(enabled=enabled),
    )
    result = client.decide(decision_request)
    assert calls == 1
    assert result.primary.status == status
    assert result.fallback is not None
    assert result.fallback.model is None
    if isinstance(outcome, DecisionPrediction):
        assert result.primary.usage == outcome.usage
        assert result.primary.model == outcome.model


def test_timeout_and_cancellation(decision_request: DecisionRequest) -> None:
    """Timeout falls back, cancellation drains the provider without fallback."""

    async def scenario() -> None:
        started = asyncio.Event()
        drained = asyncio.Event()
        calls = 0

        class Sleeping:
            async def predict(self, inner: DecisionRequest) -> DecisionPrediction:
                assert inner.name
                started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    drained.set()
                raise AssertionError

        async def counted(inner: DecisionRequest) -> DecisionAnswer:
            nonlocal calls
            calls += 1
            return await baseline(inner)

        client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
            Sleeping(),
            fallback=counted,
            config=DecisionConfig(timeout_seconds=0.01),
        )
        result = await client.adecide(decision_request)
        assert result.primary.status == 'timeout'
        assert drained.is_set()
        assert calls == 1
        drained.clear()
        started.clear()
        task = asyncio.create_task(client.adecide(decision_request))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 1
        assert drained.is_set()

    asyncio.run(scenario())


def test_baseline_failure_drains_batch(decision_request: DecisionRequest) -> None:
    """A failing baseline cancels and awaits other active batch callbacks."""

    async def scenario() -> None:
        started = asyncio.Event()
        drained = asyncio.Event()

        async def failing(inner: DecisionRequest) -> DecisionAnswer:
            if inner.name == 'route':
                await started.wait()
                message = 'baseline failed'
                raise RuntimeError(message)
            started.set()
            try:
                await asyncio.Future[None]()
            finally:
                drained.set()
            raise AssertionError

        client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
            StubProvider(),
            fallback=failing,
            config=DecisionConfig(enabled=False),
        )
        with pytest.raises(RuntimeError, match='baseline failed'):
            await client.abatch(
                [decision_request, decision_request.model_copy(update={'name': 'other'})],
                max_concurrency=2,
            )
        assert drained.is_set()

    asyncio.run(scenario())


def test_order_bound_and_batch_cancellation(decision_request: DecisionRequest) -> None:
    """Batch preserves order, bounds active calls, and drains on cancellation."""

    async def scenario() -> None:
        active = 0
        maximum = 0

        class Provider:
            async def predict(self, inner: DecisionRequest) -> DecisionPrediction:
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                try:
                    await asyncio.sleep(0)
                    return DecisionPrediction(
                        value='yes' if inner.name == 'route' else 'no',
                        confidence=0.9,
                        provider='fake',
                        model='fake-model',
                    )
                finally:
                    active -= 1

        client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
            Provider(), fallback=baseline, config=DecisionConfig()
        )
        result = await client.abatch(
            [
                decision_request,
                decision_request.model_copy(update={'name': 'other'}),
                decision_request,
            ],
            max_concurrency=2,
        )
        assert [item.value for item in result] == ['yes', 'no', 'yes']
        assert maximum == len((0, 1))
        assert active == 0
        for limit in (0, -1, True):
            with pytest.raises(ValueError, match='positive integer'):
                await client.abatch([decision_request], max_concurrency=limit)

    asyncio.run(scenario())


def test_safe_baseline_validation(decision_request: DecisionRequest) -> None:
    """Bad baseline categories raise a content-free caller error."""

    async def invalid(inner: DecisionRequest) -> DecisionAnswer:
        assert inner.name
        return DecisionAnswer(value='secret-invalid-value', provider='local')

    client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        StubProvider(),
        fallback=invalid,
        config=DecisionConfig(enabled=False),
    )
    with pytest.raises(ValueError, match='Baseline returned an invalid decision answer') as caught:
        client.decide(decision_request)
    assert 'secret-invalid-value' not in str(caught.value)


def test_immutable_and_strict(decision_request: DecisionRequest) -> None:
    """Models reject mutation, loose types and a configured model name."""
    with pytest.raises(ValidationError):
        decision_request.state = 'changed'
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate({'model': 'any-model'})
    with pytest.raises(ValidationError):
        DecisionConfig(timeout_seconds=float('nan'))
    with pytest.raises(ValidationError):
        DecisionUsage(input_tokens=True)
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate(
            {
                **decision_request.model_dump(),
                'choices': (decision_request.choices[0], decision_request.choices[0]),
            }
        )
    assert 'public example' not in repr(decision_request)


def _reply(body: dict[str, object], value: str = 'no', **usage: int) -> dict[str, object]:
    """Return a valid platform reply for one request body."""
    return decided(
        body,
        lambda _question, _state: (value, 0.95),
        usage=cast('dict[str, int | None]', usage or {'input_tokens': 8, 'output_tokens': 2}),
    )


def _allow(_request: DecisionRequest) -> bool:
    """Authorize the offline default request."""
    return True


def _client(platform: OfflinePlatform, authorize: object = _allow) -> DecisionClient:
    return DecisionClient(
        fallback=baseline,
        config=DecisionConfig(),
        authorize=authorize,  # type: ignore[arg-type]
        client=platform.client(),
    )


@pytest.mark.parametrize('allowed', [False, True])
def test_authorization_complete_request(
    decision_request: DecisionRequest, *, allowed: bool
) -> None:
    """Authorization sees frozen full content before the exact platform request."""
    seen: list[DecisionRequest] = []

    def authorize(inner: DecisionRequest) -> bool:
        seen.append(inner)
        assert inner == decision_request
        return allowed

    platform = OfflinePlatform(lambda body: httpx.Response(200, json=_reply(body)))
    client = _client(platform, authorize)
    result = client.decide(decision_request)
    assert len(seen) == 1
    assert len(platform.requests) == int(allowed)
    assert result.primary.status == ('accepted' if allowed else 'blocked')
    if allowed:
        assert platform.requests[0].url.path == '/v1/decisions'
        assert result.primary.model == PLATFORM_MODEL
        assert result.primary.usage == DecisionUsage(input_tokens=8, output_tokens=2)
        assert client.decide(decision_request).source == 'primary'


@pytest.mark.parametrize(
    'change',
    [
        'missing_model',
        'undeclared_value',
        'answer_invalid',
        'answer_status',
        'missing_confidence',
        'nan_confidence',
        'usage',
        'unknown_usage',
        'http_error',
        'redirect',
        'authorization_error',
    ],
)
def test_platform_validation(decision_request: DecisionRequest, change: str) -> None:  # noqa: C901 - wire corruption matrix.
    """Invalid replies and HTTP failures fall back without exposing response content."""

    def build(body: dict[str, object]) -> dict[str, object]:
        reply = _reply(body)
        (answer,) = cast('list[dict[str, object]]', reply['answers'])
        if change == 'missing_model':
            del reply['model']
        elif change == 'undeclared_value':
            answer['value'] = 'undeclared'
        elif change == 'answer_invalid':
            answer.update(status='invalid', value=None, confidence=None)
        elif change == 'answer_status':
            answer['status'] = 'maybe'
        elif change == 'missing_confidence':
            del answer['confidence']
        elif change == 'nan_confidence':
            answer['confidence'] = float('nan')
        elif change == 'usage':
            reply['usage'] = {'input_tokens': True}
        elif change == 'unknown_usage':
            del reply['usage']
        return reply

    def authorize(inner: DecisionRequest) -> bool:
        assert inner.name
        if change == 'authorization_error':
            message = 'sensitive authorizer detail'
            raise RuntimeError(message)
        return True

    def respond(body: dict[str, object]) -> httpx.Response:
        if change == 'http_error':
            return httpx.Response(503, text='secret response body')
        if change == 'redirect':
            return httpx.Response(307, headers={'Location': 'https://example.com'})
        return httpx.Response(200, json=build(body))

    result = _client(OfflinePlatform(respond), authorize).decide(decision_request)
    if change == 'unknown_usage':
        assert result.source == 'primary'
        assert result.primary.usage is None
    else:
        assert result.source == 'fallback'
        assert 'secret' not in repr(result)
        if change in {'missing_model', 'undeclared_value', 'answer_invalid', 'missing_confidence'}:
            assert result.primary.usage == DecisionUsage(input_tokens=8, output_tokens=2)


def test_direct_platform_nonpublic_blocks_before_authorizer(
    decision_request: DecisionRequest,
) -> None:
    """The transport itself rejects private content before approval or network."""
    authorizer_calls = 0

    def authorize(inner: DecisionRequest) -> bool:
        nonlocal authorizer_calls
        assert inner.name
        authorizer_calls += 1
        return False

    platform = OfflinePlatform(lambda body: httpx.Response(200, json=_reply(body)))
    provider = PlatformDecisions(authorize=authorize, client=platform.client())
    for privacy in ('private', 'unknown'):
        with pytest.raises(Exception, match='blocked'):
            asyncio.run(provider.predict(decision_request.model_copy(update={'privacy': privacy})))
    assert authorizer_calls == 0
    with pytest.raises(Exception, match='blocked'):
        asyncio.run(provider.predict(decision_request))
    assert authorizer_calls == 1
    assert platform.calls == []


def test_caller_batch_cancellation(decision_request: DecisionRequest) -> None:
    """Caller cancellation drains active calls and never starts queued fallbacks."""

    async def scenario() -> None:
        entered = asyncio.Event()
        active = 0
        fallbacks = 0

        class Provider:
            async def predict(self, request: DecisionRequest) -> DecisionPrediction:
                nonlocal active
                assert request.name
                active += 1
                entered.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    active -= 1
                raise AssertionError

        async def counted(inner: DecisionRequest) -> DecisionAnswer:
            nonlocal fallbacks
            fallbacks += 1
            return await baseline(inner)

        client = DecisionClient._from_backend(Provider(), fallback=counted, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        task = asyncio.create_task(client.abatch([decision_request] * 3, max_concurrency=1))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0
        assert fallbacks == 0

    asyncio.run(scenario())


@pytest.mark.parametrize('answers', [None, [], {}])
def test_malformed_answers_keep_usage(decision_request: DecisionRequest, answers: object) -> None:
    """A parseable envelope with the wrong answer set keeps its usage receipt."""

    def respond(body: dict[str, object]) -> httpx.Response:
        reply = _reply(body)
        reply['answers'] = answers
        return httpx.Response(200, json=reply)

    result = _client(OfflinePlatform(respond)).decide(decision_request)
    assert result.primary.status == 'invalid'
    expected = DecisionUsage(input_tokens=8, output_tokens=2) if answers == [] else None
    assert result.primary.usage == expected
