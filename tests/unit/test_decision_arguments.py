# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Typed argument preparation stays bounded and fail closed."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from maivn._internal.decisions.arguments import (
    DecisionArgument,
    DecisionArgumentPreparation,
    DecisionArgumentPreparer,
)
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
    DecisionResult,
)


def question(name: str = 'route', privacy: str = 'public') -> DecisionRequest:
    """Build an explicitly authorized categorical question."""
    return DecisionRequest(
        name=name,
        state='sensitive context',
        instructions='Choose',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
        privacy=cast('Literal["public", "private", "unknown"]', privacy),
    )


class Provider:
    """Observe bounded requests without network access."""

    def __init__(self, *, confidence: float = 0.95, fail: bool = False) -> None:
        """Configure a controlled primary outcome."""
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.confidence = confidence
        self.fail = fail

    async def predict(self, request: DecisionRequest) -> DecisionPrediction:
        """Return a delayed negative category."""
        assert request.name
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail:
                raise RuntimeError
            return DecisionPrediction(
                value='no', confidence=self.confidence, provider='fake', model='fake-model'
            )
        finally:
            self.active -= 1


async def fallback(request: DecisionRequest) -> DecisionAnswer:
    """Keep the full original question available to the baseline."""
    assert request.state == 'sensitive context'
    return DecisionAnswer(value='yes', provider='local')


def preparer(provider: Provider) -> DecisionArgumentPreparer:
    """Use explicit local client policy."""
    return DecisionArgumentPreparer(
        DecisionClient._from_backend(provider, fallback=fallback, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
    )


def test_lazy_definition_and_exact_typed_values() -> None:
    """Definitions are lazy and false never becomes a string or truthy value."""
    provider = Provider()
    runner = preparer(provider)
    arguments = [
        DecisionArgument('flag', question(), {'yes': True, 'no': False}),
        DecisionArgument('literal', question(), {'yes': 'yes', 'no': 'false'}),
    ]
    assert provider.calls == 0
    result = runner.prepare(arguments)
    assert result.values == {'flag': False, 'literal': 'false'}
    assert type(result.values['flag']) is bool
    assert list(result.decisions) == ['flag', 'literal']


@pytest.mark.parametrize(
    'values',
    [
        {'yes': True},
        {'yes': True, 'no': False, 'x': True},
        {'yes': 1, 'no': False},
        {'yes': None, 'no': False},
    ],
)
def test_invalid_value_configuration(values: object) -> None:
    """Every category maps explicitly to a strict boolean or string."""
    with pytest.raises((TypeError, ValueError)):
        DecisionArgument('flag', question(), cast('dict[str, bool | str]', values))


@pytest.mark.parametrize('name', ['', 'two words', '1flag', 'class', 1])
def test_invalid_argument_names(name: object) -> None:
    """Argument names must be valid nonkeyword Python identifiers."""
    with pytest.raises((TypeError, ValueError)):
        DecisionArgument(cast('str', name), question(), {'yes': True, 'no': False})


def test_collection_validation_precedes_inference() -> None:
    """Malformed later declarations cannot start earlier provider work."""
    provider = Provider()
    runner = preparer(provider)
    arg = DecisionArgument('flag', question(), {'yes': True, 'no': False})
    bad = DecisionArgument('bad', question(), {'yes': True, 'no': False})
    object.__setattr__(bad, 'values', {'yes': 1, 'no': False})
    for args in ([arg, arg], [arg, bad], [arg, object()], 'invalid', {'flag': arg}):
        with pytest.raises((TypeError, ValueError)):
            runner.prepare(cast('list[DecisionArgument]', args))
    for limit in (True, 0, -1, 1.5):
        with pytest.raises(ValueError, match='positive integer'):
            runner.prepare([arg], max_concurrency=cast('int', limit))
    assert provider.calls == 0


def test_question_revalidated_before_inference() -> None:
    """Pydantic unchecked copies cannot carry invalid questions into tasks."""
    invalid = question().model_copy(update={'privacy': 'bad'})
    with pytest.raises(ValidationError):
        DecisionArgument('flag', invalid, {'yes': True, 'no': False})
    provider = Provider()
    arg = DecisionArgument('flag', question(), {'yes': True, 'no': False})
    object.__setattr__(arg, 'question', invalid)
    with pytest.raises(ValidationError):
        preparer(provider).prepare([arg])
    assert provider.calls == 0


def test_ordered_bounded_batch() -> None:
    """Many arguments preserve declaration order within one bounded batch."""
    provider = Provider()
    args = [
        DecisionArgument(f'field_{i}', question(str(i)), {'yes': True, 'no': False})
        for i in range(7)
    ]
    max_concurrency = 2
    result = asyncio.run(preparer(provider).aprepare(args, max_concurrency=max_concurrency))
    assert list(result.values) == [arg.name for arg in args]
    assert provider.peak == max_concurrency
    assert provider.calls == len(args)


@pytest.mark.parametrize('privacy', ['private', 'unknown'])
def test_privacy_fallback_is_diagnostic_only(privacy: str) -> None:
    """Blocked inputs bypass primary and cannot prefill larger workflow fields."""
    provider = Provider()
    result = preparer(provider).prepare(
        [DecisionArgument('flag', question(privacy=privacy), {'yes': True, 'no': False})]
    )
    assert provider.calls == 0
    assert not result.values
    assert result.decisions['flag'].source == 'fallback'
    assert result.decisions['flag'].primary.status == 'blocked'


@pytest.mark.parametrize(
    ('confidence', 'fail', 'status'), [(0.1, False, 'low_confidence'), (0.95, True, 'unavailable')]
)
def test_failed_primary_leaves_field_unresolved(
    confidence: float, *, fail: bool, status: str
) -> None:
    """Accepted baseline categories remain diagnostics when primary fails."""
    result = preparer(Provider(confidence=confidence, fail=fail)).prepare(
        [DecisionArgument('flag', question(), {'yes': True, 'no': False})]
    )
    assert not result.values
    assert result.decisions['flag'].primary.status == status
    assert result.decisions['flag'].fallback is not None


def test_detached_immutable_config_results_and_repr() -> None:
    """Caller edits and shallow record mutation cannot change prepared records."""
    values = {'yes': True, 'no': False}
    arg = DecisionArgument('flag', question(), values)
    values['no'] = True
    result = preparer(Provider()).prepare([arg])
    assert result.values['flag'] is False
    with pytest.raises(TypeError):
        cast('dict[str, bool | str]', arg.values)['no'] = True
    with pytest.raises(TypeError):
        cast('dict[str, bool | str]', result.values)['flag'] = True
    with pytest.raises(TypeError):
        cast('dict[str, DecisionResult]', result.decisions)['flag'] = result.decisions['flag']
    with pytest.raises(FrozenInstanceError):
        arg.name = 'other'  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.decisions['flag'].primary.confidence = 0.0
    assert 'sensitive context' not in repr(arg)
    assert 'false' not in repr(result)
    copied = DecisionArgumentPreparation(values, result.decisions)
    values['yes'] = False
    assert copied.values['yes'] is True


def test_failure_and_cancellation_drain_siblings() -> None:
    """Baseline exceptions and cancellation propagate only after siblings drain."""

    async def scenario(*, cancel: bool) -> None:
        started = asyncio.Event()
        drained = asyncio.Event()

        class Sleeping:
            async def predict(self, request: DecisionRequest) -> DecisionPrediction:
                if request.name == 'fail':
                    await started.wait()
                    raise RuntimeError
                started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    drained.set()
                raise AssertionError

        async def broken(request: DecisionRequest) -> DecisionAnswer:
            assert request.name == 'fail'
            raise LookupError

        runner = DecisionArgumentPreparer(
            DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
                Sleeping(),
                fallback=broken,
                config=DecisionConfig(timeout_seconds=60.0),
            )
        )
        names = ['wait', 'wait2'] if cancel else ['wait', 'fail']
        task = asyncio.create_task(
            runner.aprepare(
                [
                    DecisionArgument(name, question(name), {'yes': True, 'no': False})
                    for name in names
                ]
            )
        )
        await started.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else LookupError):
            await task
        assert drained.is_set()

    asyncio.run(scenario(cancel=False))
    asyncio.run(scenario(cancel=True))


def test_timeout_remains_unresolved() -> None:
    """Timed out primary requests retain fallback history without a typed value."""

    class Sleeping:
        async def predict(self, request: DecisionRequest) -> DecisionPrediction:
            assert request.name
            await asyncio.Future[None]()
            raise AssertionError

    runner = DecisionArgumentPreparer(
        DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
            Sleeping(),
            fallback=fallback,
            config=DecisionConfig(timeout_seconds=0.01),
        )
    )
    result = runner.prepare([DecisionArgument('flag', question(), {'yes': True, 'no': False})])
    assert not result.values
    assert result.decisions['flag'].primary.status == 'timeout'
