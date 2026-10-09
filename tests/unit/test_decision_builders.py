# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Tests for lazy bound decision stages."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING, cast

import pytest

from maivn._internal.decisions.flow import DecisionInvocationBuilder
from maivn._internal.models import InvokeResponse
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
)
from maivn.messages import AIMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from maivn._internal.decisions.flow_models import DecisionContext, DecisionQuestion
    from maivn._internal.models import StreamEvent


_INPUT_TOKENS = 9


def request() -> DecisionRequest:
    """Return an explicitly public categorical question."""
    return DecisionRequest(
        name='process',
        state='Synthetic public data',
        instructions='Choose',
        choices=(
            DecisionChoice(key='yes', description='Process'),
            DecisionChoice(key='no', description='Skip'),
        ),
        privacy='public',
    )


class Provider:
    """Produce a controlled category without network traffic."""

    def __init__(self, value: str = 'yes', confidence: float = 0.95) -> None:
        """Store the requested outcome."""
        self.value = value
        self.confidence = confidence
        self.calls = 0

    async def predict(self, question: DecisionRequest) -> DecisionPrediction:
        """Count an inference and return its declared answer."""
        assert question.name
        self.calls += 1
        return DecisionPrediction(
            value=self.value, confidence=self.confidence, provider='fake', model='fake-model'
        )


async def fallback(question: DecisionRequest) -> DecisionAnswer:
    """Keep the current invocation path on primary rejection."""
    assert question.name
    return DecisionAnswer(value='no', provider='baseline')


def client(provider: Provider) -> DecisionClient:
    """Construct a provider-isolated decision client."""
    return DecisionClient._from_backend(provider, fallback=fallback, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.


class Scope:
    """Capture invocation messages and kwargs."""

    def __init__(self) -> None:
        """Prepare a canonical response."""
        self.calls: list[tuple[object, dict[str, object]]] = []
        self.response = InvokeResponse(
            final_message=AIMessage(content='done').to_contract(),
            session_id='session',
            root_event_id='event',
            event_positions=[1],
            response='done',
            usage={'input_tokens': _INPUT_TOKENS},
        )

    async def ainvoke(self, messages: object, **kwargs: object) -> InvokeResponse:
        """Return the unchanged response."""
        self.calls.append((messages, kwargs))
        return self.response

    async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        """Streaming tests provide their own scope."""
        assert messages is not None
        assert kwargs is not None
        raise AssertionError
        yield


def test_definition_lazy_and_negative_skip() -> None:
    """Defining a primary gate makes no inference; a valid negative skips."""
    provider = Provider('no')
    scope = Scope()
    plan = DecisionInvocationBuilder(scope, client(provider)).decide(
        'gate', questions={'process': request()}, require={'process': 'yes'}
    )
    assert provider.calls == 0
    result = asyncio.run(plan.ainvoke('original'))
    assert result.status == 'skipped'
    assert result.skipped_by == 'gate'
    assert result.response is None
    assert not scope.calls


def test_fallback_never_skips_existing_invocation() -> None:
    """A negative baseline preserves the existing invocation path."""
    provider = Provider('no', 0.1)
    scope = Scope()
    plan = DecisionInvocationBuilder(scope, client(provider)).decide(
        'gate', questions={'process': request()}, require={'process': 'yes'}
    )
    result = plan.invoke('original', origin='test')
    assert result.status == 'completed'
    assert result.response is scope.response
    assert scope.calls == [('original', {'origin': 'test'})]
    assert result.decisions['gate']['process'].source == 'fallback'


def test_context_mapping_and_after_order() -> None:
    """Later stages see prior named results and original input after explicit mapping."""
    scope = Scope()
    seen: list[tuple[str, object, object]] = []

    def second(context: DecisionContext) -> DecisionRequest:
        seen.append(('before', context.input, context.output))
        assert context.decisions['first']['process'].value == 'yes'
        return request()

    def after(context: DecisionContext) -> DecisionRequest:
        seen.append(('after', context.input, context.output))
        assert context.output is not None
        assert context.output.response == 'done'
        assert set(context.decisions) == {'first', 'second'}
        return request()

    plan = (
        DecisionInvocationBuilder(scope, client(Provider()))
        .decide('first', questions={'process': request()})
        .decide('second', questions={'process': second})
        .map_input(lambda context: f'Mapped {context.decisions["first"]["process"].value}')
        .decide('review', at='after', questions={'process': after})
    )
    result = plan.invoke('Original', origin='forwarded')
    assert scope.calls == [('Mapped yes', {'origin': 'forwarded'})]
    assert [(at, original) for at, original, _ in seen] == [
        ('before', 'Original'),
        ('after', 'Original'),
    ]
    assert set(result.decisions) == {'first', 'second', 'review'}
    assert result.response is scope.response


@pytest.mark.parametrize(
    'problem',
    [
        'duplicate',
        'empty',
        'placement',
        'after_gate',
        'unknown_question',
        'unknown_value',
        'bad_name',
        'bad_question',
        'concurrency',
    ],
)
def test_invalid_declarations_no_inference(problem: str) -> None:
    """Invalid plan definitions fail before inference or scope execution."""
    provider = Provider()
    plan = DecisionInvocationBuilder(Scope(), client(provider)).decide(
        'first', questions={'process': request()}
    )
    with pytest.raises((ValueError, TypeError)):  # noqa: PT012 - invalid declaration matrix.
        if problem == 'duplicate':
            plan.decide('first', questions={'process': request()})
        elif problem == 'empty':
            plan.decide('second', questions={})
        elif problem == 'placement':
            plan.decide('second', questions={'process': request()}, at='during')
        elif problem == 'after_gate':
            plan.decide(
                'second', questions={'process': request()}, at='after', require={'process': 'yes'}
            )
        elif problem == 'unknown_question':
            plan.decide('second', questions={'process': request()}, require={'missing': 'yes'})
        elif problem == 'unknown_value':
            plan.decide(
                'second', questions={'process': request()}, require={'process': 'undeclared'}
            )
        elif problem == 'bad_name':
            plan.decide('invalid name', questions={'process': request()})
        elif problem == 'bad_question':
            plan.decide('second', questions={'invalid name': request()})
        else:
            plan.decide('second', questions={'process': request()}, max_concurrency=True)
    assert provider.calls == 0


def test_factory_gate_validated_before_dispatch() -> None:
    """An invalid later factory prevents every call in the same stage."""
    provider = Provider()
    plan = DecisionInvocationBuilder(Scope(), client(provider)).decide(
        'gate',
        questions={'first': request(), 'second': lambda _context: request()},
        require={'second': 'undeclared'},
    )
    with pytest.raises(ValueError, match='declared choice'):
        plan.invoke('original')
    assert provider.calls == 0


def test_declarations_results_and_input_immutable() -> None:
    """Caller mapping mutation cannot alter the frozen plan or completed records."""
    questions: dict[str, DecisionQuestion] = {'process': request()}
    gates = {'process': 'yes'}
    scope = Scope()
    plan = DecisionInvocationBuilder(scope, client(Provider())).decide(
        'first', questions=questions, require=gates
    )
    questions.clear()
    gates['process'] = 'no'
    result = plan.invoke({'content': ['local private text']})
    assert result.status == 'completed'
    with pytest.raises(TypeError):
        cast('dict[str,object]', result.decisions)['new'] = {}
    with pytest.raises(TypeError):
        cast('dict[str,object]', result.decisions['first'])['new'] = None
    with pytest.raises(FrozenInstanceError):
        setattr(plan, '_client', client(Provider()))  # noqa: B010 - intentionally test frozen assignment.


def test_same_stage_output_snapshots_independent() -> None:
    """One local factory cannot mutate another independent factory's output."""
    scope = Scope()

    def mutate(context: DecisionContext) -> DecisionRequest:
        assert context.output is not None
        context.output.usage['input_tokens'] = 999
        return request()

    def observe(context: DecisionContext) -> DecisionRequest:
        assert context.output is not None
        assert context.output.usage['input_tokens'] == _INPUT_TOKENS
        return request()

    plan = DecisionInvocationBuilder(scope, client(Provider())).decide(
        'review', at='after', questions={'mutate': mutate, 'observe': observe}
    )
    assert plan.invoke('Original').status == 'completed'
    assert scope.response.usage['input_tokens'] == _INPUT_TOKENS


def test_batch_global_operation_bound_and_isolation() -> None:
    """Questions and scope operations share one global ceiling across ordered items."""

    async def scenario() -> None:
        active = maximum = 0

        class Counted(Provider):
            async def predict(self, question: DecisionRequest) -> DecisionPrediction:
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                try:
                    await asyncio.sleep(0)
                    return await super().predict(question)
                finally:
                    active -= 1

        class CountedScope(Scope):
            async def ainvoke(self, messages: object, **kwargs: object) -> InvokeResponse:
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                try:
                    await asyncio.sleep(0)
                    return await super().ainvoke(messages, **kwargs)
                finally:
                    active -= 1

        scope = CountedScope()
        seen: list[object] = []

        def factory(context: DecisionContext) -> DecisionRequest:
            seen.append(context.input)
            assert not context.decisions
            return request()

        plan = DecisionInvocationBuilder(scope, client(Counted())).decide(
            'gate',
            questions={'first': factory, 'second': request(), 'third': request()},
            max_concurrency=3,
        )
        results = await plan.abatch(['one', 'two', 'three'], max_concurrency=2)
        assert len(results) == len(seen)
        assert maximum == len((0, 1))
        assert active == 0
        assert seen == ['one', 'two', 'three']
        assert results[0].decisions is not results[1].decisions

    asyncio.run(scenario())


def test_iterator_failure_has_no_execution() -> None:
    """Failing input enumeration creates no detached invocation tasks."""

    async def scenario() -> None:
        scope = Scope()
        provider = Provider()

        def inputs() -> Iterator[str]:
            yield 'one'
            message = 'input enumeration failed'
            raise RuntimeError(message)

        plan = DecisionInvocationBuilder(scope, client(provider)).decide(
            'gate', questions={'process': request()}
        )
        existing = asyncio.all_tasks()
        with pytest.raises(RuntimeError, match='enumeration failed'):
            await plan.abatch(inputs())
        assert asyncio.all_tasks() == existing
        assert provider.calls == 0
        assert not scope.calls

    asyncio.run(scenario())


def test_batch_failure_cancels_and_drains_other_items() -> None:
    """The current baseline's failure propagates after other work drains."""

    async def scenario() -> None:
        entered = asyncio.Event()
        drained = asyncio.Event()

        class FailingScope(Scope):
            async def ainvoke(self, messages: object, **kwargs: object) -> InvokeResponse:
                assert kwargs is not None
                if messages == 'fail':
                    await entered.wait()
                    message = 'scope failed'
                    raise RuntimeError(message)
                entered.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    drained.set()
                raise AssertionError

        plan = DecisionInvocationBuilder(FailingScope(), client(Provider()))
        with pytest.raises(RuntimeError, match='scope failed'):
            await plan.abatch(['fail', 'pending'])
        assert drained.is_set()

    asyncio.run(scenario())


def test_stage_cancellation_drains_all_questions() -> None:
    """Cancelling a stage cancels every active question without triggering baseline."""

    async def scenario() -> None:
        started = asyncio.Event()
        active = 0
        baseline_calls = 0

        class Waiting(Provider):
            async def predict(self, question: DecisionRequest) -> DecisionPrediction:
                nonlocal active
                assert question.name
                active += 1
                started.set()
                try:
                    await asyncio.Future[None]()
                finally:
                    active -= 1
                raise AssertionError

        async def counted(question: DecisionRequest) -> DecisionAnswer:
            nonlocal baseline_calls
            baseline_calls += 1
            return await fallback(question)

        decisions = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
            Waiting(), fallback=counted, config=DecisionConfig()
        )
        scope = Scope()
        plan = DecisionInvocationBuilder(scope, decisions).decide(
            'gate', questions={'one': request(), 'two': request()}
        )
        task = asyncio.create_task(plan.ainvoke('original'))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0
        assert baseline_calls == 0
        assert not scope.calls

    asyncio.run(scenario())


def test_bad_factory_type_and_mapping_error_no_invoke() -> None:
    """Callback contract errors propagate without inventing decisions or response."""
    provider = Provider()
    scope = Scope()

    def bad(context: DecisionContext) -> DecisionRequest:
        assert context.input
        return cast('DecisionRequest', 'invalid result')

    plan = DecisionInvocationBuilder(scope, client(provider)).decide(
        'gate', questions={'one': request(), 'two': bad}
    )
    with pytest.raises(TypeError, match='DecisionRequest'):
        plan.invoke('original')
    assert provider.calls == 0

    def bad_mapping(context: DecisionContext) -> object:
        assert context.input
        message = 'local mapping failed'
        raise RuntimeError(message)

    plan = DecisionInvocationBuilder(scope, client(provider)).map_input(bad_mapping)
    with pytest.raises(RuntimeError, match='mapping failed'):
        plan.invoke('original')
    assert not scope.calls
