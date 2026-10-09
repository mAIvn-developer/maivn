# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Shared candidate selection preserves private and uncertain retrieval baselines."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from maivn._internal.decisions.platform import PlatformDecisions
from maivn._internal.decisions.runtime import DecisionClient
from maivn.decisions import (
    DecisionAnswer,
    DecisionCandidateMatcher,
    DecisionConfig,
    DecisionMatchCandidate,
    DecisionMatchConfig,
    DecisionMatchRequest,
    DecisionPrediction,
    DecisionRequest,
    candidate_match_questions,
)
from tests.unit.decision_platform import OfflinePlatform, decided

INPUT_TOKENS = 17


def _unused_client() -> DecisionClient:
    def forbidden(_: dict[str, Any]) -> httpx.Response:
        pytest.fail('Gated matching reached transport')

    async def fallback(_: object) -> DecisionAnswer:
        pytest.fail('Gated matching reached fallback')

    return DecisionClient(
        fallback=fallback,
        config=DecisionConfig(),
        authorize=lambda _: True,
        client=OfflinePlatform(forbidden).client(),
    )


def test_disabled_preserves_exact_baseline() -> None:
    """Disabled selection performs no client work and does not cap the baseline."""
    request = DecisionMatchRequest(
        query='query',
        candidates=(
            DecisionMatchCandidate(key='one', text='text'),
            DecisionMatchCandidate(key='two', text='other'),
        ),
        baseline_keys=('two', 'one'),
        max_results=1,
    )
    result = DecisionCandidateMatcher(_unused_client(), DecisionMatchConfig()).match(request)
    assert result.selected_keys == ('two', 'one')
    assert result.source == 'baseline'
    assert result.decisions == ()


def public_request() -> DecisionMatchRequest:
    """Return local source IDs and an exact baseline that never enters model state."""
    return DecisionMatchRequest(
        query='Which excerpt answers?',
        candidates=(
            DecisionMatchCandidate(key='PRIVATE_THREAD_SOURCE_1', text='First excerpt'),
            DecisionMatchCandidate(key='PRIVATE_THREAD_SOURCE_2', text='Second excerpt'),
        ),
        baseline_keys=('PRIVATE_THREAD_SOURCE_2',),
        privacy='public',
        max_results=1,
    )


@pytest.mark.parametrize('mode', ['yes', 'none', 'low', 'invalid', 'outage', 'unknown'])
def test_native_selection_and_exact_abstention(mode: str) -> None:
    """Accepted decisions map locally; every uncertain outcome preserves retrieval."""
    request = public_request()
    approved = candidate_match_questions(request)
    calls = 0

    async def fallback(_: object) -> DecisionAnswer:
        return DecisionAnswer(value='no', provider='local')

    def respond(body: dict[str, Any]) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert 'PRIVATE_THREAD_SOURCE' not in json.dumps(body)
        if mode == 'outage':
            return httpx.Response(503)
        value = 'no' if mode == 'none' else 'yes'

        def choose(question: dict[str, Any], _state: str) -> tuple[str, float] | str:
            if mode == 'invalid':
                return 'invalid'
            if mode == 'unknown' and question['name'] == 'q1':
                return ('unknown', 0.95)
            return (value, 0.1 if mode == 'low' else 0.95)

        return httpx.Response(
            200,
            json=decided(body, choose, usage={'input_tokens': 17, 'output_tokens': 2}),
        )

    client = DecisionClient(
        fallback=fallback,
        config=DecisionConfig(),
        authorize=lambda question: question in approved,
        client=OfflinePlatform(respond).client(),
    )
    result = DecisionCandidateMatcher(client, DecisionMatchConfig(enabled=True)).match(request)
    assert calls == 1
    assert result.selected_keys == (
        ('PRIVATE_THREAD_SOURCE_1',) if mode == 'yes' else request.baseline_keys
    )
    assert result.source == ('primary' if mode == 'yes' else 'baseline')
    if mode == 'unknown':
        assert all(item.primary.status == 'accepted' for item in result.decisions)
        assert result.reason == 'rejected'
    if mode != 'outage':
        assert (
            sum(
                item.primary.usage.input_tokens
                for item in result.decisions
                if item.primary.usage and item.primary.usage.input_tokens is not None
            )
            == INPUT_TOKENS
        )


@pytest.mark.parametrize('privacy', ['private', 'unknown'])
def test_privacy_precedes_serialization(monkeypatch: pytest.MonkeyPatch, privacy: str) -> None:
    """Private text never reaches JSON construction or client execution."""

    def forbidden(*_: object, **__: object) -> str:
        pytest.fail('Private input was serialized')

    monkeypatch.setattr('maivn._internal.decisions.matching.json.dumps', forbidden)
    request = public_request().model_copy(update={'privacy': privacy})
    result = DecisionCandidateMatcher(_unused_client(), DecisionMatchConfig(enabled=True)).match(
        request
    )
    assert result.selected_keys == request.baseline_keys
    assert result.reason == 'blocked'
    with pytest.raises(ValueError, match='requires public'):
        candidate_match_questions(request)


def test_bounds_and_validation() -> None:
    """Oversized states preserve the exact baseline and ambiguous identities fail."""
    request = public_request()
    result = DecisionCandidateMatcher(
        _unused_client(), DecisionMatchConfig(enabled=True, max_state_characters=1)
    ).match(request)
    assert result.selected_keys == request.baseline_keys
    assert result.reason == 'oversized'
    with pytest.raises(ValidationError):
        DecisionMatchRequest(
            query='query',
            candidates=(request.candidates[0], request.candidates[0]),
            baseline_keys=(),
        )
    with pytest.raises(ValidationError):
        DecisionMatchRequest(
            query='query', candidates=request.candidates, baseline_keys=('missing',)
        )


def test_matcher_timeout_and_cancellation_drain() -> None:
    """Total matcher deadlines preserve retrieval; caller cancellation propagates."""

    async def scenario(*, cancel: bool) -> None:
        entered = asyncio.Event()
        drained = asyncio.Event()

        async def respond(_: dict[str, Any]) -> httpx.Response:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                drained.set()
            raise AssertionError

        async def fallback(_: object) -> DecisionAnswer:
            return DecisionAnswer(value='no', provider='local')

        client = DecisionClient(
            fallback=fallback,
            config=DecisionConfig(timeout_seconds=10),
            authorize=lambda _: True,
            client=OfflinePlatform(respond).client(),
        )
        matcher = DecisionCandidateMatcher(
            client, DecisionMatchConfig(enabled=True, timeout_seconds=0.02)
        )
        task = asyncio.create_task(
            matcher.abatch([public_request(), public_request()], max_concurrency=1)
        )
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            results = await task
            assert all(
                result.reason == 'timeout'
                and result.selected_keys == public_request().baseline_keys
                for result in results
            )
        assert drained.is_set()

    asyncio.run(scenario(cancel=False))
    asyncio.run(scenario(cancel=True))


def test_match_batch_bounds_physical_calls_and_keeps_order() -> None:
    """Sequential native chunks share the global matching HTTP concurrency bound."""

    async def scenario() -> None:
        concurrency = 2
        active = 0
        peak = 0
        calls = 0

        async def respond(body: dict[str, Any]) -> httpx.Response:
            nonlocal active, peak, calls
            active += 1
            peak = max(peak, active)
            calls += 1
            try:
                await asyncio.sleep(0)
                return httpx.Response(
                    200, json=decided(body, lambda _question, _state: ('yes', 0.95))
                )
            finally:
                active -= 1

        async def fallback(_: object) -> DecisionAnswer:
            return DecisionAnswer(value='no', provider='local')

        requests = [
            public_request().model_copy(
                update={
                    'candidates': (DecisionMatchCandidate(key=f'local-{index}', text='Excerpt'),),
                    'baseline_keys': (),
                }
            )
            for index in range(5)
        ]
        approved = tuple(
            question for request in requests for question in candidate_match_questions(request)
        )
        client = DecisionClient(
            fallback=fallback,
            config=DecisionConfig(max_batch_questions=1),
            authorize=lambda question: question in approved,
            client=OfflinePlatform(respond).client(),
        )
        results = await DecisionCandidateMatcher(client, DecisionMatchConfig(enabled=True)).abatch(
            requests, max_concurrency=2
        )
        assert [result.selected_keys for result in results] == [
            (f'local-{index}',) for index in range(5)
        ]
        assert calls == len(requests)
        assert peak <= concurrency
        assert active == 0

    asyncio.run(scenario())


def test_unapproved_generated_request_preserves_baseline() -> None:
    """Full original authorization rejects changed query despite identical wire IDs."""
    approved = candidate_match_questions(public_request())

    def forbidden(_: dict[str, Any]) -> httpx.Response:
        pytest.fail('Unauthorized question reached HTTP')

    async def fallback(_: object) -> DecisionAnswer:
        return DecisionAnswer(value='no', provider='local')

    client = DecisionClient(
        fallback=fallback,
        config=DecisionConfig(),
        authorize=lambda question: question in approved,
        client=OfflinePlatform(forbidden).client(),
    )
    changed = public_request().model_copy(update={'query': 'Unapproved altered query'})
    result = DecisionCandidateMatcher(client, DecisionMatchConfig(enabled=True)).match(changed)
    assert result.selected_keys == changed.baseline_keys
    assert result.reason == 'rejected'
    assert all(item.primary.status == 'blocked' for item in result.decisions)


@pytest.mark.parametrize('mode', ['low', 'outage'])
def test_matching_never_spends_original_fallback(mode: str) -> None:
    """Abstention keeps receipts without invoking or mutating the caller baseline."""
    original_calls = 0
    request = public_request()
    approved = candidate_match_questions(request)

    async def expensive(_: object) -> DecisionAnswer:
        nonlocal original_calls
        original_calls += 1
        return DecisionAnswer(value='yes', provider='expensive-original', model='other')

    def respond(body: dict[str, Any]) -> httpx.Response:
        assert 'model' not in body
        if mode == 'outage':
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=decided(
                body,
                lambda _question, _state: ('yes', 0.1),
                usage={'input_tokens': INPUT_TOKENS, 'output_tokens': 2},
            ),
        )

    client = DecisionClient(
        fallback=expensive,
        config=DecisionConfig(),
        authorize=lambda question: question in approved,
        client=OfflinePlatform(respond).client(),
    )
    matcher = DecisionCandidateMatcher(client, DecisionMatchConfig(enabled=True))
    result = matcher.match(request)
    assert original_calls == 0
    assert result.selected_keys == request.baseline_keys
    assert result.reason == 'rejected'
    assert all(
        item.fallback
        and item.fallback.provider == 'local-matching-baseline'
        and item.fallback.model is None
        for item in result.decisions
    )
    if mode == 'low':
        assert (
            sum(
                item.primary.usage.input_tokens
                for item in result.decisions
                if item.primary.usage and item.primary.usage.input_tokens is not None
            )
            == INPUT_TOKENS
        )
    original = client.decide(approved[0])
    assert original_calls == 1
    assert original.value == 'yes'
    assert original.fallback is not None
    assert original.fallback.provider == 'expensive-original'


@pytest.mark.parametrize('native', [True, False])
def test_deadline_retains_completed_physical_receipts(*, native: bool) -> None:
    """A later unfinished request cannot erase an earlier known usage receipt."""

    async def scenario() -> None:
        calls = 0
        drained = asyncio.Event()
        expected_calls = 2
        output_tokens = 2
        approved = candidate_match_questions(public_request())

        async def respond(body: dict[str, Any]) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == expected_calls:
                try:
                    await asyncio.Future()
                finally:
                    drained.set()
                raise AssertionError
            return httpx.Response(
                200,
                json=decided(
                    body,
                    lambda _question, _state: ('yes', 0.95),
                    usage={'input_tokens': INPUT_TOKENS, 'output_tokens': output_tokens},
                ),
            )

        provider = PlatformDecisions(
            authorize=lambda question: question in approved,
            client=OfflinePlatform(respond).client(),
        )

        class SingleProvider:
            async def predict(self, request: DecisionRequest) -> DecisionPrediction:
                return await provider.predict(request)

        async def forbidden(_: DecisionRequest) -> DecisionAnswer:
            pytest.fail('Original fallback must remain unused')

        client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
            provider if native else SingleProvider(),
            fallback=forbidden,
            config=DecisionConfig(max_batch_questions=1, timeout_seconds=10),
        )
        result = await DecisionCandidateMatcher(
            client, DecisionMatchConfig(enabled=True, timeout_seconds=0.05)
        ).amatch(public_request())
        assert calls == expected_calls
        assert drained.is_set()
        assert result.reason == 'timeout'
        assert result.selected_keys == public_request().baseline_keys
        assert len(result.decisions) == 1
        usage = result.decisions[0].primary.usage
        assert usage is not None
        assert usage.input_tokens == INPUT_TOKENS
        assert usage.output_tokens == output_tokens
        assert result.decisions[0].source == 'primary'

    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['low', 'outage', 'unknown'])
def test_rejected_packet_skips_remaining_spend(mode: str) -> None:
    """One abstention determines baseline restoration without another paid packet."""
    calls = 0
    approved = candidate_match_questions(public_request())

    def respond(body: dict[str, Any]) -> httpx.Response:
        nonlocal calls
        calls += 1
        if mode == 'outage':
            return httpx.Response(503)
        selected = 'unknown' if mode == 'unknown' else 'yes'
        return httpx.Response(
            200,
            json=decided(
                body,
                lambda _question, _state: (selected, 0.1 if mode == 'low' else 0.95),
                usage={'input_tokens': INPUT_TOKENS, 'output_tokens': 2},
            ),
        )

    async def forbidden(_: DecisionRequest) -> DecisionAnswer:
        pytest.fail('Original fallback must remain unused')

    client = DecisionClient(
        fallback=forbidden,
        config=DecisionConfig(max_batch_questions=1),
        authorize=lambda question: question in approved,
        client=OfflinePlatform(respond).client(),
    )
    result = DecisionCandidateMatcher(client, DecisionMatchConfig(enabled=True)).match(
        public_request()
    )
    assert calls == 1
    assert result.reason == 'rejected'
    assert result.selected_keys == public_request().baseline_keys
    assert len(result.decisions) == 1
