# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Native batch transport and receipt conservation."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from maivn.decisions import (
    DecisionAnswer,
    DecisionArgument,
    DecisionArgumentPreparer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
)
from tests.unit.decision_platform import OfflinePlatform, decided

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

BATCH_SIZE = 2
INPUT_TOKENS = 9


def question(state: str = 'public', instructions: str = 'Choose') -> DecisionRequest:
    """Declare a public question with repeated names permitted."""
    return DecisionRequest(
        name='same',
        state=state,
        instructions=instructions,
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
    )


async def baseline(request: DecisionRequest) -> DecisionAnswer:
    """Return a local answer."""
    assert request.state
    return DecisionAnswer(value='no', provider='local')


def _yes(_question: dict[str, Any], _state: str) -> tuple[str, float]:
    return ('yes', 0.95)


def _client(
    platform: OfflinePlatform,
    *,
    config: DecisionConfig | None = None,
    fallback: Any = baseline,
    authorize: Callable[[DecisionRequest], bool] = lambda _: True,
) -> DecisionClient:
    return DecisionClient(
        fallback=fallback,
        config=config or DecisionConfig(),
        authorize=authorize,
        client=platform.client(),
    )


def test_native_transport_and_receipt() -> None:
    """Same state crosses once and shared tokens are counted once."""
    bodies: list[dict[str, object]] = []
    authorized: list[DecisionRequest] = []

    def authorize(request: DecisionRequest) -> bool:
        authorized.append(request)
        return True

    def respond(body: dict[str, Any]) -> httpx.Response:
        bodies.append(body)
        assert len(authorized) == BATCH_SIZE
        return httpx.Response(
            200, json=decided(body, _yes, usage={'input_tokens': 9, 'output_tokens': 3})
        )

    client = _client(OfflinePlatform(respond), authorize=authorize)
    requests = [question(), question(instructions='Second')]
    results = asyncio.run(client.abatch(requests, native_batch=True))
    assert len(bodies) == 1
    assert authorized == requests
    assert [result.value for result in results] == ['yes', 'yes']
    assert (
        sum(
            result.primary.usage.input_tokens
            for result in results
            if result.primary.usage and result.primary.usage.input_tokens is not None
        )
        == INPUT_TOKENS
    )
    assert all(
        result.primary.usage_scope == 'batch' and result.primary.usage_batch_size == BATCH_SIZE
        for result in results
    )


def test_denied_original_prevents_entire_http_chunk() -> None:
    """A repeated approved name never authorizes a different original question."""
    requests = [question(), question(instructions='Denied')]
    seen: list[DecisionRequest] = []

    def authorize(request: DecisionRequest) -> bool:
        seen.append(request)
        return request.instructions != 'Denied'

    def respond(_: dict[str, Any]) -> httpx.Response:
        message = 'Unauthorized HTTP request'
        raise AssertionError(message)

    client = _client(OfflinePlatform(respond), authorize=authorize)
    results = asyncio.run(client.abatch(requests, native_batch=True))
    assert seen == requests
    assert all(
        result.primary.status == 'blocked' and result.source == 'fallback' for result in results
    )


@pytest.mark.parametrize('failure', ['low', 'invalid', 'missing', 'extra', 'undeclared'])
def test_failed_questions_keep_one_receipt(failure: str) -> None:
    """Invalid envelopes and individual answers keep the aggregate receipt once."""
    fallback_calls: list[DecisionRequest] = []

    async def counted(request: DecisionRequest) -> DecisionAnswer:
        fallback_calls.append(request)
        return await baseline(request)

    def choose(item: dict[str, Any], _state: str) -> tuple[str, float] | str:
        if item['name'] != 'q1':
            return ('yes', 0.95)
        if failure == 'low':
            return ('yes', 0.1)
        if failure == 'invalid':
            return 'invalid'
        if failure == 'undeclared':
            return ('maybe', 0.95)
        return ('yes', 0.95)

    def respond(body: dict[str, Any]) -> httpx.Response:
        reply = decided(body, choose, usage={'input_tokens': 9, 'output_tokens': 3})
        if failure == 'missing':
            reply['answers'] = [a for a in reply['answers'] if a['name'] != 'q1']
        elif failure == 'extra':
            reply['answers'].append({**reply['answers'][1], 'name': 'other'})
        return httpx.Response(200, json=reply)

    client = _client(OfflinePlatform(respond), fallback=counted)
    results = asyncio.run(client.abatch([question(), question()], native_batch=True))
    assert len(fallback_calls) == (1 if failure in {'low', 'invalid', 'undeclared'} else 2)
    assert (
        sum(
            result.primary.usage.input_tokens
            for result in results
            if result.primary.usage and result.primary.usage.input_tokens is not None
        )
        == INPUT_TOKENS
    )
    assert results[1].primary.usage is None


def test_chunks_keep_unlike_states_separate() -> None:
    """Question chunks are bounded and never expose unrelated states together."""
    bodies: list[dict[str, Any]] = []

    def respond(body: dict[str, Any]) -> httpx.Response:
        bodies.append(body)
        return httpx.Response(200, json=decided(body, _yes))

    client = _client(OfflinePlatform(respond), config=DecisionConfig(max_batch_questions=2))
    requests = [question(), question(state='other'), question(), question()]
    results = asyncio.run(client.abatch(requests, native_batch=True, max_concurrency=1))
    assert [(body['state'], len(body['questions'])) for body in bodies] == [
        ('public', 2),
        ('public', 1),
        ('other', 1),
    ]
    assert all(result.source == 'primary' for result in results)


def test_timeout_and_cancellation_drain_http() -> None:
    """Deadlines fall back once; caller cancellation propagates and drains transport."""

    async def scenario(*, cancel: bool) -> None:
        entered = asyncio.Event()
        drained = asyncio.Event()
        calls: list[DecisionRequest] = []

        async def respond(_: dict[str, Any]) -> httpx.Response:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
            message = 'Unreachable'
            raise AssertionError(message)

        async def counted(request: DecisionRequest) -> DecisionAnswer:
            calls.append(request)
            return await baseline(request)

        client = _client(
            OfflinePlatform(respond),
            config=DecisionConfig(timeout_seconds=0.02),
            fallback=counted,
        )
        task = asyncio.create_task(client.abatch([question(), question()], native_batch=True))
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not calls
        else:
            results = await task
            assert all(result.primary.status == 'timeout' for result in results)
            assert len(calls) == BATCH_SIZE
        assert drained.is_set()

    asyncio.run(scenario(cancel=False))
    asyncio.run(scenario(cancel=True))


def test_single_only_backend_compatibility() -> None:
    """A single-only backend keeps working with the opt-in flag."""

    class Single:
        """Single-question contract."""

        async def predict(self, request: DecisionRequest) -> DecisionPrediction:
            """Return a single categorical prediction."""
            assert request.name
            return DecisionPrediction(
                value='yes', confidence=0.9, provider='old', model='fake-model'
            )

    client = DecisionClient._from_backend(Single(), fallback=baseline, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
    results = client.batch([question(), question()], native_batch=True)
    assert all(result.primary.usage_scope == 'individual' for result in results)


@pytest.mark.parametrize('mode', ['default', 'private', 'disabled'])
def test_native_opt_in_and_privacy(mode: str) -> None:
    """Default batching uses single calls; private and disabled inputs stay local."""
    bodies: list[dict[str, Any]] = []

    def respond(body: dict[str, Any]) -> httpx.Response:
        bodies.append(body)
        return httpx.Response(200, json=decided(body, _yes))

    client = _client(OfflinePlatform(respond), config=DecisionConfig(enabled=mode != 'disabled'))
    requests = [question(), question()]
    if mode == 'private':
        requests = [request.model_copy(update={'privacy': 'private'}) for request in requests]
    results = client.batch(requests, native_batch=mode != 'default')
    assert len(bodies) == (BATCH_SIZE if mode == 'default' else 0)
    assert all(
        result.source == ('primary' if mode == 'default' else 'fallback') for result in results
    )


def test_native_argument_preparer() -> None:
    """Explicit argument preparation forwards native opt-in and preserves typed values."""
    bodies: list[dict[str, Any]] = []

    def respond(body: dict[str, Any]) -> httpx.Response:
        bodies.append(body)
        return httpx.Response(200, json=decided(body, _yes))

    client = _client(OfflinePlatform(respond))
    prepared = DecisionArgumentPreparer(client).prepare(
        [
            DecisionArgument('first', question(), {'yes': True, 'no': False}),
            DecisionArgument('second', question(), {'yes': 'yes', 'no': 'no'}),
        ],
        native_batch=True,
    )
    assert len(bodies) == 1
    assert dict(prepared.values) == {'first': True, 'second': 'yes'}


def test_platform_request_carries_no_model_or_question_names() -> None:
    """The wire body holds only state and synthetic question names, never a model."""
    bodies: list[dict[str, Any]] = []

    def respond(body: dict[str, Any]) -> httpx.Response:
        bodies.append(body)
        return httpx.Response(200, json=decided(body, _yes))

    client = _client(OfflinePlatform(respond))
    asyncio.run(client.abatch([question(), question()], native_batch=True))
    (body,) = bodies
    assert set(body) == {'state', 'questions'}
    assert [item['name'] for item in body['questions']] == ['q0', 'q1']
    assert 'same' not in json.dumps(body)


@pytest.mark.parametrize('failure', ['exception', 'shape', 'timeout'])
def test_unknown_native_backend_failure_does_not_claim_a_provider(failure: str) -> None:
    """Unknown failures retain truthful provider identity for in-memory backends."""

    class Custom:
        """A different decision backend with a native batch capability."""

        async def predict(self, request: DecisionRequest) -> DecisionPrediction:
            """Preserve the single capability without invoking it for this batch."""
            assert request.name
            return DecisionPrediction(
                value='yes', provider='custom', model='fake-model', confidence=0.9
            )

        async def predict_batch(self, requests: Sequence[DecisionRequest]) -> list[Any]:
            """Produce a failure before any attested provider metadata exists."""
            assert requests
            if failure == 'timeout':
                await asyncio.Future()
            if failure == 'shape':
                return []
            message = 'Synthetic custom provider failure'
            raise RuntimeError(message)

    client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        Custom(), fallback=baseline, config=DecisionConfig(timeout_seconds=0.02)
    )
    results = client.batch([question(), question()], native_batch=True)
    assert all(result.source == 'fallback' for result in results)
    assert all(result.primary.provider is None for result in results)
    assert all(result.primary.usage is None for result in results)
