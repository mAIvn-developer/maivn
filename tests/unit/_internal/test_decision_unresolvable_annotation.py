"""A managed decision target with an unresolvable annotation still runs (U05-D16)."""

# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam the suite uses.
from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel

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
    decision_flow,
    depends_on_decision,
)


class _Payload(BaseModel):
    """A model argument the managed call validates from a mapping."""

    amount: int


class _Provider:
    async def predict(self, question: DecisionRequest) -> DecisionPrediction:
        assert question.name
        return DecisionPrediction(
            value='yes',
            confidence=0.99,
            provider='fake',
            model='fake-model',
            usage=DecisionUsage(input_tokens=1, output_tokens=1),
        )


async def _fallback(question: DecisionRequest) -> DecisionAnswer:
    assert question.name
    return DecisionAnswer(value='no', provider='baseline')


def _client() -> DecisionClient:
    return DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage]
        cast('Any', _Provider()), fallback=_fallback, config=DecisionConfig()
    )


def _step() -> DecisionStep:
    request = DecisionRequest(
        name='check',
        state='public synthetic',
        instructions='Choose',
        choices=(
            DecisionChoice(key='yes', description='Run'),
            DecisionChoice(key='no', description='Skip'),
        ),
        privacy='public',
    )
    return DecisionStep('allow', questions={'answer': request})


def test_a_type_checking_only_annotation_does_not_break_the_managed_call() -> None:
    """Declaration already tolerated it; invoke now does too, with the caller's value."""

    def target(other: object = None, *, decisions: DecisionResults) -> object:
        return (other, sorted(decisions))

    target.__annotations__ = {'other': 'OnlyForTypeCheckers', 'decisions': DecisionResults}
    decorated = depends_on_decision(client=_client(), decisions=[_step()], arg_name='decisions')(
        target
    )

    outcome = decision_flow(decorated).invoke({'other': 'kept'})

    assert (outcome.status, outcome.value) == ('completed', ('kept', ['allow']))


def test_resolvable_string_annotations_are_still_validated() -> None:
    """A string hint that resolves is still validated when another one cannot resolve."""

    def target(other: object, payload: object, *, decisions: DecisionResults) -> object:
        return (other, payload, sorted(decisions))

    target.__annotations__ = {
        'other': 'OnlyForTypeCheckers',
        'payload': '_Payload',
        'decisions': DecisionResults,
    }
    decorated = depends_on_decision(client=_client(), decisions=[_step()], arg_name='decisions')(
        target
    )

    outcome = decision_flow(decorated).invoke({'other': 'kept', 'payload': {'amount': 3}})

    assert outcome.value == ('kept', _Payload(amount=3), ['allow'])


def test_every_hint_a_string_still_validates_the_resolvable_ones() -> None:
    """With postponed annotations and no injection argument, one bad hint drops only itself."""

    def target(payload: object, other: object = None) -> object:
        return (payload, other)

    target.__annotations__ = {'payload': '_Payload', 'other': 'OnlyForTypeCheckers | None'}
    decorated = depends_on_decision(client=_client(), decisions=[_step()])(target)

    outcome = decision_flow(decorated).invoke({'payload': {'amount': 5}})

    assert outcome.value == (_Payload(amount=5), None)
