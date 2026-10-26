# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam the suite already uses.
"""Characterization tests for managed decision dependencies and their validation."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
    DecisionResults,
    DecisionStep,
    DecisionUsage,
    TokenCategoryUsage,
    TokenUsageByCategory,
    decision_flow,
    depends_on_decision,
)

_DECISIONS_ATTR = '__maivn_decision_dependencies__'


def _request(name: str = 'check') -> DecisionRequest:
    return DecisionRequest(
        name=name,
        state='public synthetic',
        instructions='Choose',
        choices=(
            DecisionChoice(key='yes', description='Run'),
            DecisionChoice(key='no', description='Skip'),
        ),
        privacy='public',
    )


class _Provider:
    """Confident in-memory decision provider that reports token usage."""

    def __init__(self) -> None:
        self.calls = 0

    async def predict(self, question: DecisionRequest) -> DecisionPrediction:
        self.calls += 1
        assert question.name
        return DecisionPrediction(
            value='yes',
            confidence=0.99,
            provider='fake',
            model='fake-model',
            usage=DecisionUsage(input_tokens=7, output_tokens=2),
        )


def _client(provider: _Provider | None = None) -> DecisionClient:
    async def fallback(question: DecisionRequest) -> DecisionAnswer:
        assert question.name
        return DecisionAnswer(value='no', provider='baseline')

    return DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage]
        cast('Any', provider or _Provider()), fallback=fallback, config=DecisionConfig()
    )


def _step(name: str = 'allow', **kwargs: Any) -> DecisionStep:
    return DecisionStep(name, questions={'answer': _request()}, **kwargs)


@pytest.mark.parametrize(
    ('kwargs', 'error', 'message'),
    [
        ({'max_concurrency': 0}, ValueError, 'max_concurrency must be a positive integer'),
        ({'max_concurrency': True}, ValueError, 'max_concurrency must be a positive integer'),
        ({'decisions': []}, ValueError, 'Decision dependencies require at least one step'),
        ({'decisions': ['allow']}, TypeError, 'Decision declarations must be DecisionStep records'),
        (
            {
                'decisions': [
                    DecisionStep('allow', questions={'a': _request()}, after=cast('Any', ['x']))
                ]
            },
            TypeError,
            'Decision predecessors must be a name or tuple of names',
        ),
        (
            {
                'decisions': [
                    DecisionStep('allow', questions={'a': _request()}, after=cast('Any', ('x', 1)))
                ]
            },
            TypeError,
            'Decision predecessors must be a name or tuple of names',
        ),
    ],
)
def test_declaration_errors_are_raised_eagerly(
    kwargs: dict[str, Any], error: type[Exception], message: str
) -> None:
    """Invalid limits, empty plans, and malformed steps fail when the decorator is built."""
    arguments: dict[str, Any] = {'client': _client(), 'decisions': [_step()], **kwargs}

    with pytest.raises(error) as raised:
        _ = depends_on_decision(**arguments)

    assert str(raised.value) == message


def test_invalid_target_shapes_are_rejected_at_decoration() -> None:
    """Positional-only parameters and missing injection arguments are refused."""

    def positional(value: int, /, *, decisions: DecisionResults) -> int:
        return value + len(decisions)

    def missing(value: int) -> int:
        return value

    with pytest.raises(
        TypeError,
        match=r'^Managed decision functions require explicit keyword-compatible parameters$',
    ):
        _ = depends_on_decision(client=_client(), decisions=[_step()], arg_name='decisions')(
            positional
        )
    with pytest.raises(
        ValueError, match=r'^Decision injection requires an existing keyword-compatible argument$'
    ):
        _ = depends_on_decision(client=_client(), decisions=[_step()], arg_name='decisions')(
            missing
        )
    assert not hasattr(missing, _DECISIONS_ATTR)


def test_failed_second_declaration_restores_the_first() -> None:
    """A rejected extra decorator leaves the earlier declaration in force."""
    provider = _Provider()

    def target(value: int = 3) -> int:
        return value

    decorated = depends_on_decision(client=_client(provider), decisions=[_step('allow')])(target)
    with pytest.raises(ValueError, match=r'^Decision stage names must be globally unique$'):
        _ = depends_on_decision(client=_client(provider), decisions=[_step('allow')])(decorated)

    outcome = decision_flow(decorated).invoke({})

    assert (outcome.status, outcome.value, list(outcome.decisions)) == ('completed', 3, ['allow'])
    assert provider.calls == 1


@pytest.mark.parametrize('metadata', ['not-a-list', ['not-a-dependency']])
def test_corrupted_metadata_is_refused(metadata: object) -> None:
    """Managed entry points reject metadata that was not produced by the decorator."""

    def target() -> int:
        return 1

    setattr(target, _DECISIONS_ATTR, metadata)

    with pytest.raises(
        TypeError, match=r'^Decision dependency metadata must contain validated declarations$'
    ):
        _ = decision_flow(target)


def test_unresolvable_annotations_fall_back_to_raw_signature_annotations() -> None:
    """A type-checking-only annotation elsewhere does not block declaration validation."""

    def target(other: object = None, *, decisions: DecisionResults) -> object:
        return (other, sorted(decisions))

    target.__annotations__ = {'other': 'OnlyForTypeCheckers', 'decisions': DecisionResults}

    decorated = depends_on_decision(client=_client(), decisions=[_step()], arg_name='decisions')(
        target
    )

    # Managed invocation of such a target is pinned in test_decision_unresolvable_annotation.py.
    assert decorated is target
    assert decision_flow(decorated).target is target


def test_completed_call_reports_decision_token_usage() -> None:
    """The managed call result sums the decision receipts it collected."""

    def target() -> str:
        return 'ran'

    decorated = depends_on_decision(client=_client(), decisions=[_step('one'), _step('two')])(
        target
    )

    outcome = decision_flow(decorated).invoke({})

    assert outcome.value == 'ran'
    assert outcome.token_usage_by_category == TokenUsageByCategory(
        llm=TokenCategoryUsage(
            input_tokens=0, output_tokens=0, known_input_tokens=0, known_output_tokens=0
        ),
        decision=TokenCategoryUsage(
            input_tokens=14, output_tokens=4, known_input_tokens=14, known_output_tokens=4
        ),
    )


def test_undecorated_targets_run_directly_including_batches() -> None:
    """A target without decisions is dispatched as-is, singly and in ordered batches."""

    def double(value: int) -> int:
        return value * 2

    flow = decision_flow(double)

    single = flow.invoke({'value': 2})
    batch = flow.batch([{'value': 1}, {'value': 5}], max_concurrency=1)
    abatch = asyncio.run(flow.abatch([{'value': 4}]))

    assert (single.status, single.value, dict(single.decisions)) == ('completed', 4, {})
    assert [item.value for item in batch] == [2, 10]
    assert [item.value for item in abatch] == [8]
    with pytest.raises(ValueError, match=r'^max_concurrency must be a positive integer$'):
        _ = flow.batch([], max_concurrency=0)


def test_variadic_targets_cannot_be_managed() -> None:
    """Managed calls bind by keyword, so variadic targets are refused at call time."""

    def variadic(*values: int) -> int:
        return sum(values)

    with pytest.raises(
        TypeError,
        match=r'^Managed decision functions require explicit keyword-compatible parameters$',
    ):
        _ = decision_flow(variadic).invoke({})
