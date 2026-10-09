# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Decisions compose with existing scope wrappers without changing their defaults."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from maivn_contracts.messages import Message
from pydantic import BaseModel

from maivn import Agent, Swarm
from maivn._internal.compat.invocation import coerce_structured_response
from maivn._internal.models import InvokeResponse, StreamEvent
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from maivn.decisions import DecisionContext


class Primary:
    """Return a fixed accepted answer without inference."""

    async def predict(self, request: DecisionRequest) -> DecisionPrediction:
        """Accept the declared ready answer without a provider call."""
        assert request.name
        return DecisionPrediction(
            value='yes', provider='synthetic', model='fake-model', confidence=1.0
        )


async def fallback(request: DecisionRequest) -> DecisionAnswer:
    """Retain the existing admission result."""
    assert request.name
    return DecisionAnswer(value='yes', provider='existing-workflow')


def client() -> DecisionClient:
    """Build an explicitly configured local test client."""
    return DecisionClient._from_backend(Primary(), fallback=fallback, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.


def request() -> DecisionRequest:
    """Use a fixed invented public request."""
    return DecisionRequest(
        name='ready',
        state='invented public input',
        instructions='Is it ready?',
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='Ready'),
            DecisionChoice(key='no', description='Not ready'),
        ),
    )


class Answer(BaseModel):
    """Typed original invocation output."""

    answer: str


@pytest.mark.parametrize('structured_first', [False, True])
def test_after_decision_sees_structured_result(
    monkeypatch: pytest.MonkeyPatch, *, structured_first: bool
) -> None:
    """Neither wrapper order loses decisions or structured validation."""
    seen: list[str] = []

    async def invoke(self: Agent, messages: object, **kwargs: object) -> InvokeResponse:
        assert self.name
        assert messages == 'public input'
        seen.append('invoke')
        response = InvokeResponse(
            final_message=Message(
                message_id='synthetic-result',
                role='assistant',
                content='{"answer":"ready"}',
                ts='2026-09-30T00:00:00Z',
            ),
            session_id='synthetic-session',
            root_event_id='synthetic-event',
            event_positions=[1],
            response='{"answer":"ready"}',
        )
        model = kwargs.get('structured_output')
        return coerce_structured_response(
            response, model if isinstance(model, type) and issubclass(model, BaseModel) else None
        )

    monkeypatch.setattr(Agent, 'ainvoke', invoke)
    agent = Agent(name='synthetic-agent', api_key='synthetic-key')

    def after(context: DecisionContext) -> DecisionRequest:
        assert context.output is not None
        assert isinstance(context.output.result, Answer)
        seen.append(context.output.result.answer)
        return request()

    if structured_first:
        plan = agent.structured_output(Answer).decide(
            'review', client=client(), questions={'ready': after}, at='after'
        )
    else:
        plan = agent.decide(
            'review', client=client(), questions={'ready': after}, at='after'
        ).structured_output(Answer)
    outcome = asyncio.run(plan.ainvoke('public input'))
    assert outcome.status == 'completed'
    assert outcome.response is not None
    assert isinstance(outcome.response.result, Answer)
    assert outcome.decisions['review']['ready'].value == 'yes'
    assert seen == ['invoke', 'ready']


@pytest.mark.parametrize('surface', ['agent', 'swarm', 'events', 'followup'])
def test_entrypoints_are_lazy(surface: str) -> None:
    """Creating a decision plan through a wrapper performs no model or scope work."""
    agent = Agent(name='synthetic-agent', api_key='synthetic-key')
    if surface == 'agent':
        plan = agent.decide('route', client=client(), questions={'ready': request()})
    elif surface == 'swarm':
        plan = Swarm(name='synthetic-swarm').decide(
            'route', client=client(), questions={'ready': request()}
        )
    elif surface == 'events':
        plan = agent.events().decide('route', client=client(), questions={'ready': request()})
    else:
        plan = agent.allow_followup_questions().decide(
            'route', client=client(), questions={'ready': request()}
        )
    assert plan is not None


@pytest.mark.parametrize('wrapper', ['structured', 'events'])
def test_followup_after_wrapped_decision_plan(wrapper: str) -> None:
    """Appending follow-up policy preserves an already declared decision plan."""
    agent = Agent(name='synthetic-agent', api_key='synthetic-key')
    plan = agent.decide('route', client=client(), questions={'ready': request()})
    plan = plan.structured_output(Answer) if wrapper == 'structured' else plan.events()
    configured = plan.allow_followup_questions(max_questions=2)
    assert configured is not plan
    assert configured is not None


@pytest.mark.parametrize('wrapper', ['structured', 'events', 'followup'])
@pytest.mark.parametrize('wrapper_first', [False, True])
def test_decision_wrapper_close_preserves_cleanup(
    monkeypatch: pytest.MonkeyPatch, wrapper: str, *, wrapper_first: bool
) -> None:
    """Both declaration orders close the source without executing after decisions."""
    closed: list[bool] = []
    reviewed: list[bool] = []

    async def source(self: Agent, messages: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        assert self.name
        assert messages == 'public input'
        assert kwargs['cancel_on_close'] is True
        try:
            yield StreamEvent(position=1, event_type='content.delta', data={'text': 'partial'})
            await asyncio.Future[None]()
        finally:
            closed.append(True)

    def after(context: DecisionContext) -> DecisionRequest:
        reviewed.append(context.output is not None)
        return request()

    monkeypatch.setattr(Agent, 'astream', source)

    async def scenario() -> None:
        agent = Agent(name='synthetic-agent', api_key='synthetic-key')
        if wrapper_first:
            if wrapper == 'structured':
                plan = agent.structured_output(Answer).decide(
                    'review', client=client(), questions={'ready': after}, at='after'
                )
            elif wrapper == 'events':
                plan = agent.events().decide(
                    'review', client=client(), questions={'ready': after}, at='after'
                )
            else:
                plan = agent.allow_followup_questions().decide(
                    'review', client=client(), questions={'ready': after}, at='after'
                )
        else:
            plan = agent.decide('review', client=client(), questions={'ready': after}, at='after')
            if wrapper == 'structured':
                plan = plan.structured_output(Answer)
            elif wrapper == 'events':
                plan = plan.events()
            else:
                plan = plan.allow_followup_questions()
        events = plan.astream('public input', cancel_on_close=True)
        assert (await anext(events)).event_type == 'content.delta'
        await events.aclose()
        assert closed == [True]
        assert reviewed == []

    asyncio.run(scenario())
