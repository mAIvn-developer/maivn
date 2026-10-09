"""Closing an invocation wrapper deterministically closes its underlying stream."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from maivn import Agent
from maivn._internal.models import StreamEvent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator


class Answer(BaseModel):
    """Minimal structured output declared for an unfinished stream."""

    answer: str


@pytest.mark.parametrize('wrapper', ['structured', 'events', 'followup'])
def test_wrapper_aclose_closes_source(monkeypatch: pytest.MonkeyPatch, wrapper: str) -> None:
    """Source cleanup has completed when outer aclose returns, without relying on GC."""
    closed: list[bool] = []

    async def source(self: Agent, messages: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        assert self.name
        assert messages
        assert kwargs is not None
        try:
            yield StreamEvent(position=1, event_type='content.delta', data={'text': 'partial'})
            await asyncio.sleep(60)
        finally:
            closed.append(True)

    monkeypatch.setattr(Agent, 'astream', source)

    async def scenario() -> None:
        agent = Agent(name='synthetic-agent', api_key='synthetic-key')
        if wrapper == 'structured':
            events = agent.structured_output(Answer).astream('input')
        elif wrapper == 'events':
            events = agent.events().astream('input')
        else:
            events = agent.allow_followup_questions().astream('input')
        await anext(events)
        await cast('AsyncGenerator[StreamEvent, None]', events).aclose()
        assert closed == [True]

    asyncio.run(scenario())
