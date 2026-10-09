"""Decision streams forward live content and complete after local annotations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.decisions.flow import DecisionInvocationBuilder
from maivn._internal.decisions.flow_models import DecisionContext, DecisionStreamEvent
from maivn._internal.models import RunOptions, StreamEvent
from tests.unit.test_decision_builders import Provider, Scope, client, request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from maivn.decisions import DecisionRequest


class StreamingScope(Scope):
    """Forward a real terminal fixture and track iterator cleanup."""

    def __init__(self, *, fail: bool = False, stall: bool = False) -> None:
        """Prepare deterministic ordinary invocation events."""
        super().__init__()
        self.closed = False
        self.fail = fail
        self.stall = stall
        self.events = [
            StreamEvent(position=1, event_type='chunk', data={'payload': {'content': 'partial'}}),
            StreamEvent(
                position=2,
                event_type='session.completed',
                data={
                    'session_id': 'session',
                    'payload': {
                        'final_message': self.response.final_message.model_dump(mode='json'),
                        'usage': self.response.usage,
                        'result': {'answer': 'ready'},
                    },
                },
            ),
        ]

    async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        """Yield ordinary events before final plan completion."""
        self.calls.append((messages, kwargs))
        try:
            yield self.events[0]
            if self.stall:
                await asyncio.Future[None]()
            if self.fail:
                message = 'original stream failure'
                raise RuntimeError(message)
            yield self.events[1]
        finally:
            self.closed = True


class Answer(BaseModel):
    """Validated final structured answer."""

    answer: str


def test_stream_order_final_outcome_and_serialization() -> None:
    """Final invocation event precedes after decisions and excluded local outcome."""
    scope = StreamingScope()
    seen: list[str] = []

    def after(context: DecisionContext) -> DecisionRequest:
        assert context.output is not None
        assert context.output.usage == scope.response.usage
        seen.append('after')
        return request()

    plan = (
        DecisionInvocationBuilder(scope, client(Provider()))
        .decide('gate', questions={'process': request()})
        .decide('review', questions={'process': after}, at='after')
    )
    events = list(plan.stream('original'))
    assert [event.event_type for event in events] == [
        'decision.stage.started',
        'decision.stage.completed',
        'chunk',
        'session.completed',
        'decision.stage.started',
        'decision.stage.completed',
        'decision.plan.completed',
    ]
    final = events[-1]
    assert isinstance(final, DecisionStreamEvent)
    assert final.outcome is not None
    assert final.outcome.status == 'completed'
    assert final.outcome.response == response_from_stream_events(scope.events)
    assert seen == ['after']
    assert 'outcome' not in final.model_dump()
    assert 'original' not in final.model_dump_json()
    assert scope.closed


def test_stream_negative_skip_has_no_underlying_request() -> None:
    """A rejected primary gate emits one final skipped outcome without scope work."""
    scope = StreamingScope()
    plan = DecisionInvocationBuilder(scope, client(Provider('no'))).decide(
        'gate', questions={'process': request()}, require={'process': 'yes'}
    )
    events = list(plan.stream('original'))
    assert not scope.calls
    assert isinstance(events[-1], DecisionStreamEvent)
    assert events[-1].outcome is not None
    assert events[-1].outcome.status == 'skipped'


@pytest.mark.parametrize('close_after_terminal', [False, True])
def test_early_close_skips_after_stages(*, close_after_terminal: bool) -> None:
    """Closing even immediately after the invocation terminal never runs annotations."""

    async def scenario() -> None:
        scope = StreamingScope()
        provider = Provider()
        plan = DecisionInvocationBuilder(scope, client(provider)).decide(
            'review', questions={'process': request()}, at='after'
        )
        source = plan.astream('original', cancel_on_close=True)
        assert (await anext(source)).event_type == 'chunk'
        if close_after_terminal:
            assert (await anext(source)).event_type == 'session.completed'
        await source.aclose()
        assert scope.closed
        assert provider.calls == 0
        assert scope.calls[0][1]['cancel_on_close'] is True

    asyncio.run(scenario())


def test_stream_failure_no_after_stages() -> None:
    """The original stream error propagates without annotations or invented completion."""

    async def scenario() -> None:
        scope = StreamingScope(fail=True)
        provider = Provider()
        plan = DecisionInvocationBuilder(scope, client(provider)).decide(
            'review', questions={'process': request()}, at='after'
        )
        with pytest.raises(RuntimeError, match='original stream failure'):
            _ = [event async for event in plan.astream('original')]
        assert scope.closed
        assert provider.calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize('direct', [False, True])
def test_stream_keyword_structure_and_thread_context(*, direct: bool) -> None:
    """After context gets a validated output and the same resolved thread ID."""

    async def scenario() -> None:
        scope = StreamingScope()
        seen: list[str] = []

        def after(context: DecisionContext) -> DecisionRequest:
            assert context.output is not None
            assert isinstance(context.output.result, Answer)
            assert context.output.thread_id == 'chosen-thread'
            seen.append(context.output.result.answer)
            return request()

        plan = DecisionInvocationBuilder(scope, client(Provider())).decide(
            'review', questions={'process': after}, at='after'
        )
        kwargs: dict[str, object] = {'structured_output': Answer}
        kwargs['thread_id' if direct else 'options'] = (
            'chosen-thread' if direct else RunOptions(thread_id='chosen-thread')
        )
        events = [event async for event in plan.astream('original', **kwargs)]
        assert seen == ['ready']
        assert isinstance(events[-1], DecisionStreamEvent)
        assert events[-1].outcome is not None
        assert events[-1].outcome.response is not None
        assert isinstance(events[-1].outcome.response.result, Answer)

    asyncio.run(scenario())


def test_cancel_during_stream_closes_source() -> None:
    """Caller cancellation waits for the underlying streaming iterator to close."""

    async def scenario() -> None:
        scope = StreamingScope(stall=True)
        provider = Provider()
        plan = DecisionInvocationBuilder(scope, client(provider)).decide(
            'review', questions={'process': request()}, at='after'
        )
        source = plan.astream('original')
        await anext(source)

        async def next_event() -> StreamEvent:
            return await anext(source)

        task = asyncio.create_task(next_event())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert scope.closed
        assert provider.calls == 0

    asyncio.run(scenario())
