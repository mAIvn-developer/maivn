# ruff: noqa: PLR2004, S106 - Hand-derived counts and non-secret capability labels.
"""Catch mixed token categories, duplicate batch receipts and false zero usage."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from maivn_contracts.messages import Message
from pydantic import ValidationError

from maivn import InvokeResponse
from maivn.decisions import (
    DecisionAnswer,
    DecisionAttempt,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionInvocationResult,
    DecisionPrediction,
    DecisionRequest,
    DecisionResult,
    DecisionStatus,
    DecisionUsage,
    summarize_decision_usage,
)
from tests.unit.decision_platform import choosing

if TYPE_CHECKING:
    from maivn.decisions import TokenUsageCategory


def result(*, unknown: bool = False) -> DecisionResult:
    """One physical decision followed by an ordinary model fallback."""
    return DecisionResult(
        value='yes',
        source='fallback',
        primary=DecisionAttempt(
            status=DecisionStatus.TIMEOUT if unknown else DecisionStatus.LOW_CONFIDENCE,
            latency_seconds=0.1,
            usage=None if unknown else DecisionUsage(input_tokens=9, output_tokens=1),
        ),
        fallback=DecisionAttempt(
            status=DecisionStatus.ACCEPTED,
            latency_seconds=1.0,
            token_category='llm',
            usage=DecisionUsage(input_tokens=40, output_tokens=3),
        ),
    )


def test_decision_and_larger_model_are_separate() -> None:
    """Combining decision and fallback tokens into one bucket would fail this."""
    usage = summarize_decision_usage([result()])
    assert usage.decision.input_tokens == 9
    assert usage.decision.total_tokens == 10
    assert usage.llm.input_tokens == 40
    assert usage.llm.total_tokens == 43
    assert usage.model_dump()['decision']['known_input_tokens'] == 9


def test_missing_receipt_does_not_erase_known_subtotal_or_become_zero() -> None:
    """A timed-out provider may have billed; missing counters are unknown."""
    usage = summarize_decision_usage([result(), result(unknown=True)])
    assert usage.decision.input_tokens is None
    assert usage.decision.output_tokens is None
    assert usage.decision.total_tokens is None
    assert usage.decision.known_input_tokens == 9
    assert usage.decision.unknown_input_calls == 1
    assert usage.llm.total_tokens == 86


def test_native_batch_receipt_is_owned_once() -> None:
    """Followers must not inflate counters or make a known batch incomplete."""
    question = DecisionRequest(
        name='route',
        state='synthetic public input',
        instructions='Choose',
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
    )

    platform = choosing(
        lambda _question, _state: ('yes', 0.95),
        usage={'input_tokens': 9, 'output_tokens': 1},
    )

    async def fallback(_: DecisionRequest) -> DecisionAnswer:
        return DecisionAnswer(value='no', provider='local', token_category=None)

    client = DecisionClient(
        fallback=fallback,
        config=DecisionConfig(),
        authorize=lambda _: True,
        client=platform.client(),
    )
    decisions = asyncio.run(client.abatch([question, question], native_batch=True))
    usage = summarize_decision_usage(decisions)
    assert usage.decision.total_tokens == 10
    assert usage.decision.unknown_input_calls == 0
    assert usage.llm.total_tokens == 0
    assert [decision.primary.usage_owner for decision in decisions] == [True, False]


def test_skip_reports_decision_tokens_and_zero_invocation_tokens() -> None:
    """Skipping an invocation must not hide the gate's cost or invent LLM use."""
    gate = DecisionResult(
        value='no',
        source='primary',
        primary=DecisionAttempt(
            status=DecisionStatus.ACCEPTED,
            latency_seconds=0.1,
            usage=DecisionUsage(input_tokens=9, output_tokens=1),
        ),
    )
    outcome = DecisionInvocationResult(
        status='skipped', response=None, decisions={'route': {'needed': gate}}, skipped_by='route'
    )
    assert outcome.token_usage_by_category.decision.total_tokens == 10
    assert outcome.token_usage_by_category.llm.total_tokens == 0


def test_flow_adds_invocation_without_changing_legacy_token_counts() -> None:
    """The legacy surface stays LLM-only; the additive breakdown includes both."""
    response = InvokeResponse(
        final_message=Message.model_validate(
            {
                'message_id': 'final',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-10-02T12:00:00Z',
            }
        ),
        session_id='session',
        root_event_id='root',
        event_positions=[1],
        response='done',
        usage={'input_tokens': 100, 'output_tokens': 5},
    )
    outcome = DecisionInvocationResult(
        status='completed', response=response, decisions={'review': {'ok': result()}}
    )
    assert outcome.token_usage_by_category.llm.total_tokens == 148
    assert outcome.token_usage_by_category.decision.total_tokens == 10
    assert response.token_usage is not None
    assert response.token_usage['total_tokens'] == 105
    assert response.token_usage_by_category.llm.total_tokens == 105


def test_backup_category_uses_capability_not_provider_name() -> None:
    """A future decision backup may share a brand with a regular LLM."""
    alternate = result().model_copy(
        update={
            'fallback': DecisionAttempt(
                status=DecisionStatus.ACCEPTED,
                latency_seconds=0.1,
                provider='openai',
                token_category='decision',
                usage=DecisionUsage(input_tokens=4, output_tokens=0),
            )
        }
    )
    usage = summarize_decision_usage([alternate])
    assert usage.decision.total_tokens == 14
    assert usage.llm.total_tokens == 0


def test_private_gate_and_local_fallback_have_no_model_tokens() -> None:
    """Blocked inference and a declared local fallback are verified zero work."""
    local = DecisionResult(
        value='yes',
        source='fallback',
        primary=DecisionAttempt(
            status=DecisionStatus.BLOCKED,
            latency_seconds=0,
        ),
        fallback=DecisionAttempt(
            status=DecisionStatus.ACCEPTED, latency_seconds=0, token_category=None
        ),
    )
    usage = summarize_decision_usage([local])
    assert usage.decision.total_tokens == 0
    assert usage.llm.total_tokens == 0


@pytest.mark.parametrize('category', ['llm', 'decision', None])
def test_runtime_preserves_the_declared_fallback_category(
    category: TokenUsageCategory | None,
) -> None:
    """Removing fallback capability transfer would mix Luna into decision usage."""
    question = DecisionRequest(
        name='route',
        state='public synthetic input',
        instructions='Choose',
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
    )

    async def fallback(_: DecisionRequest) -> DecisionAnswer:
        return DecisionAnswer(
            value='yes',
            provider='any-provider',
            token_category=category,
            usage=DecisionUsage(input_tokens=40, output_tokens=3) if category else None,
        )

    client = DecisionClient(
        fallback=fallback,
        config=DecisionConfig(enabled=False),
        authorize=lambda _: False,
        client=choosing(lambda _question, _state: ('yes', 0.95)).client(),
    )
    outcome = client.decide(question)
    usage = outcome.token_usage_by_category
    assert outcome.fallback is not None
    assert outcome.fallback.token_category == category
    assert usage.llm.total_tokens == (43 if category == 'llm' else 0)
    assert usage.decision.total_tokens == (43 if category == 'decision' else 0)


def test_partial_decision_receipt_keeps_the_known_counter() -> None:
    """One absent counter must not erase the other or invent a complete total."""
    partial = DecisionResult(
        value='yes',
        source='primary',
        primary=DecisionAttempt(
            status=DecisionStatus.ACCEPTED,
            latency_seconds=0,
            usage=DecisionUsage(input_tokens=9),
        ),
    )
    usage = partial.token_usage_by_category.decision
    assert usage.input_tokens == 9
    assert usage.output_tokens is None
    assert usage.total_tokens is None
    assert usage.unknown_input_calls == 0
    assert usage.unknown_output_calls == 1


def test_primary_prediction_cannot_claim_regular_model_category() -> None:
    """Primary and fallback capabilities stay unambiguous at the SDK boundary."""
    with pytest.raises(ValidationError):
        DecisionPrediction(
            value='yes', provider='test', model='test', confidence=0.9, token_category='llm'
        )


def test_local_fallback_cannot_hide_a_reported_model_receipt() -> None:
    """A local declaration with counters would make real work look free."""
    with pytest.raises(ValidationError):
        DecisionAnswer(
            value='yes', provider='test', token_category=None, usage=DecisionUsage(input_tokens=9)
        )
