"""Native batching stays opt-in and obeys stage/global concurrency bounds."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from maivn import Agent, Swarm
from maivn._internal.compat.invocation import (
    EventInvocationBuilder,
    StructuredOutputInvocationBuilder,
)
from maivn._internal.decisions.dependencies import DecisionStep, decision_flow, depends_on_decision
from maivn._internal.decisions.flow import DecisionInvocationBuilder
from maivn._internal.decisions.flow_models import DecisionStreamEvent
from maivn.decisions import (
    DecisionAnswer,
    DecisionClient,
    DecisionConfig,
    DecisionRequest,
)
from tests.unit.decision_platform import OfflinePlatform, decided
from tests.unit.test_decision_builder_streams import Answer, StreamingScope
from tests.unit.test_decision_builders import Provider, Scope, request
from tests.unit.test_decision_builders import client as single_client

if TYPE_CHECKING:
    from maivn._internal.decisions.flow_models import DecisionContext
    from maivn._internal.models import InvokeResponse, StreamEvent


_LIMIT = 2
_INPUT_TOKENS = 9


class Wire:
    """Track physical mocked requests and their shared concurrency."""

    def __init__(self, value: str = 'yes', confidence: float = 0.95) -> None:
        """Initialize synthetic categorical replies."""
        self.bodies: list[dict[str, Any]] = []
        self.value = value
        self.confidence = confidence
        self.active = 0
        self.peak = 0
        self.entered = asyncio.Event()
        self.drained = asyncio.Event()
        self.stall = False
        self.fallback_calls = 0

    async def respond(self, body: dict[str, Any]) -> httpx.Response:
        """Return one answer per actual wire question with one aggregate receipt."""
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.entered.set()
        self.bodies.append(body)
        try:
            if self.stall:
                await asyncio.Future[None]()
            await asyncio.sleep(0)
            return httpx.Response(
                200,
                json=decided(
                    body,
                    lambda _question, _state: (self.value, self.confidence),
                    usage={'input_tokens': 9, 'output_tokens': 3},
                ),
            )
        finally:
            self.active -= 1
            self.drained.set()


def native_client(wire: Wire, *, chunk_size: int = 32) -> DecisionClient:
    """Use actual SDK HTTP parsing with only public mocked data."""

    async def fallback(question: DecisionRequest) -> DecisionAnswer:
        assert question.name
        wire.fallback_calls += 1
        return DecisionAnswer(value='no', provider='existing-workflow')

    return DecisionClient(
        fallback=fallback,
        config=DecisionConfig(max_batch_questions=chunk_size),
        authorize=lambda _: True,
        client=OfflinePlatform(wire.respond).client(),
    )


@pytest.mark.parametrize('native', [False, True])
def test_native_before_opt_in_packet_and_receipt(*, native: bool) -> None:
    """Two same-state questions share one packet only when explicitly requested."""
    wire = Wire()
    scope = Scope()
    plan = DecisionInvocationBuilder(scope, native_client(wire)).decide(
        'gate', questions={'one': request(), 'two': request()}, native_batch=native
    )
    result = plan.invoke('original')
    assert result.status == 'completed'
    assert len(wire.bodies) == (1 if native else len(result.decisions['gate']))
    if native:
        assert len(wire.bodies[0]['questions']) == len(result.decisions['gate'])
        assert (
            sum(
                answer.primary.usage.input_tokens
                for answer in result.decisions['gate'].values()
                if answer.primary.usage and answer.primary.usage.input_tokens is not None
            )
            == _INPUT_TOKENS
        )


@pytest.mark.parametrize('at', ['before', 'after'])
def test_mixed_states_and_nonpublic_units(at: str) -> None:
    """Only identical public state joins a packet; private requests stay local."""
    wire = Wire()
    scope = Scope()
    questions = {
        'one': request(),
        'two': request().model_copy(update={'state': 'other-public'}),
        'private': request().model_copy(update={'privacy': 'private'}),
        'unknown': request().model_copy(update={'privacy': 'unknown'}),
    }
    plan = DecisionInvocationBuilder(scope, native_client(wire)).decide(
        'check', at=at, questions=questions, native_batch=True
    )
    result = plan.invoke('original')
    assert {body['state'] for body in wire.bodies} == {'Synthetic public data', 'other-public'}
    assert list(result.decisions['check']) == list(questions)
    assert result.decisions['check']['private'].source == 'fallback'
    assert result.decisions['check']['unknown'].source == 'fallback'


@pytest.mark.parametrize(('confidence', 'status'), [(0.95, 'skipped'), (0.1, 'completed')])
def test_primary_only_native_gate(confidence: float, status: str) -> None:
    """Native primary rejection skips; confidence fallback retains admission."""
    wire = Wire(value='no', confidence=confidence)
    scope = Scope()
    plan = DecisionInvocationBuilder(scope, native_client(wire)).decide(
        'gate',
        questions={'one': request(), 'two': request()},
        require={'one': 'yes'},
        native_batch=True,
    )
    result = plan.invoke('original')
    assert result.status == status
    assert bool(scope.calls) == (status == 'completed')
    assert len(wire.bodies) == 1


@pytest.mark.parametrize('flag', [1, 'true', None])
def test_native_flag_strict_before_execution(flag: object) -> None:
    """Invalid flags fail during declaration without any provider or scope work."""
    wire = Wire()
    with pytest.raises(ValueError, match='boolean'):
        DecisionInvocationBuilder(Scope(), native_client(wire)).decide(
            'gate', questions={'one': request()}, native_batch=cast('bool', flag)
        )
    assert not wire.bodies


@pytest.mark.parametrize('kind', ['agent', 'swarm', 'followup'])
def test_scope_forwarding(kind: str) -> None:
    """Agent, Swarm and configured follow-up scopes retain native opt-in."""
    wire = Wire(value='no')
    agent = Agent(name='test-agent', api_key='unused')
    scope = (
        agent
        if kind == 'agent'
        else Swarm(name='test-swarm', agents=[agent], api_key='unused')
        if kind == 'swarm'
        else agent.allow_followup_questions(input_handler=lambda _question, _schema: 'answer')
    )
    plan = scope.decide(
        'gate',
        client=native_client(wire),
        questions={'one': request(), 'two': request()},
        require={'one': 'yes'},
        native_batch=True,
    )
    assert plan.invoke('original').status == 'skipped'
    assert len(wire.bodies) == 1


def test_native_decorator_dependency_order() -> None:
    """Dependency groups batch independent questions without joining dependent stages."""
    wire = Wire()

    def later(context: DecisionContext) -> DecisionRequest:
        assert context.decisions['first']['one'].value == 'yes'
        return request()

    @depends_on_decision(
        client=native_client(wire),
        decisions=(
            DecisionStep('first', questions={'one': request(), 'two': request()}),
            DecisionStep('second', questions={'one': later, 'two': request()}, after='first'),
        ),
        native_batch=True,
    )
    async def target() -> str:
        return 'executed'

    result = asyncio.run(decision_flow(target).ainvoke({}))
    assert result.value == 'executed'
    assert len(wire.bodies) == len(result.decisions)
    assert all(len(body['questions']) == len(result.decisions['first']) for body in wire.bodies)


@pytest.mark.parametrize('sync', [False, True])
def test_native_stream_before_after(*, sync: bool) -> None:
    """Both streaming surfaces retain one packet per completed decision stage."""
    wire = Wire()
    scope = StreamingScope()
    seen: list[str] = []

    def after(context: DecisionContext) -> DecisionRequest:
        assert context.output is not None
        seen.append(context.output.response)
        return request()

    plan = (
        DecisionInvocationBuilder(scope, native_client(wire))
        .decide('before', questions={'one': request(), 'two': request()}, native_batch=True)
        .decide('after', questions={'one': after, 'two': request()}, at='after', native_batch=True)
    )

    async def collect() -> list[StreamEvent]:
        return [event async for event in plan.astream('original')]

    events = list(plan.stream('original')) if sync else asyncio.run(collect())
    assert len(wire.bodies) == len(('before', 'after'))
    assert seen == ['done']
    assert isinstance(events[-1], DecisionStreamEvent)
    assert events[-1].outcome is not None
    assert set(events[-1].outcome.decisions) == {'before', 'after'}


def test_native_global_bound_across_invocations_and_chunks() -> None:
    """Each outer permit admits one physical operation despite inner chunking."""

    async def scenario() -> None:
        wire = Wire()

        class CountedScope(Scope):
            async def ainvoke(self, messages: object, **kwargs: object) -> InvokeResponse:
                wire.active += 1
                wire.peak = max(wire.peak, wire.active)
                try:
                    await asyncio.sleep(0)
                    return await super().ainvoke(messages, **kwargs)
                finally:
                    wire.active -= 1

        scope = CountedScope()

        def factory(context: DecisionContext) -> DecisionRequest:
            return request().model_copy(update={'state': str(context.input)})

        plan = DecisionInvocationBuilder(scope, native_client(wire, chunk_size=1)).decide(
            'gate',
            questions={
                'one': factory,
                'two': factory,
                'third': request().model_copy(update={'state': 'other-state'}),
            },
            native_batch=True,
        )
        results = await plan.abatch(['one', 'two', 'three'], max_concurrency=_LIMIT)
        assert len(results) == len(('one', 'two', 'three'))
        assert wire.peak <= _LIMIT
        assert wire.peak == _LIMIT
        assert wire.active == 0
        assert all(len(body['questions']) == 1 for body in wire.bodies)

    asyncio.run(scenario())


def test_native_decorator_global_bound_across_groups_and_items() -> None:
    """Independent dependency groups and invocations share the same physical ceiling."""

    async def scenario() -> None:
        wire = Wire()
        decisions = native_client(wire, chunk_size=1)

        @depends_on_decision(
            client=decisions,
            decisions=(DecisionStep('a', questions={'one': request(), 'two': request()}),),
            native_batch=True,
        )
        @depends_on_decision(
            client=decisions,
            decisions=(DecisionStep('b', questions={'one': request(), 'two': request()}),),
            native_batch=True,
        )
        async def target(value: str) -> str:
            wire.active += 1
            wire.peak = max(wire.peak, wire.active)
            try:
                await asyncio.sleep(0)
                return value
            finally:
                wire.active -= 1

        results = await decision_flow(target).abatch(
            [{'value': 'one'}, {'value': 'two'}, {'value': 'three'}], max_concurrency=_LIMIT
        )
        assert [result.value for result in results] == ['one', 'two', 'three']
        assert wire.peak <= _LIMIT
        assert wire.active == 0
        assert all(len(body['questions']) == 1 for body in wire.bodies)

    asyncio.run(scenario())


def test_native_stage_cancellation_cleanup() -> None:
    """Caller cancellation drains grouped requests without invoking fallback or scope."""

    async def scenario() -> None:
        wire = Wire()
        wire.stall = True
        scope = Scope()
        plan = DecisionInvocationBuilder(scope, native_client(wire)).decide(
            'gate',
            questions={
                'one': request(),
                'two': request(),
                'other': request().model_copy(update={'state': 'other-public'}),
            },
            native_batch=True,
        )
        task = asyncio.create_task(plan.ainvoke('original'))
        await wire.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert wire.drained.is_set()
        assert wire.active == 0
        assert wire.fallback_calls == 0
        assert not scope.calls

    asyncio.run(scenario())


def test_native_factories_all_validate_before_dispatch() -> None:
    """Grouping never starts a valid sibling before a later gate validates."""
    wire = Wire()
    plan = DecisionInvocationBuilder(Scope(), native_client(wire)).decide(
        'gate',
        questions={'one': request(), 'two': lambda _context: request()},
        require={'two': 'invalid'},
        native_batch=True,
    )
    with pytest.raises(ValueError, match='declared choice'):
        plan.invoke('original')
    assert not wire.bodies


def test_decorator_native_flag_validated_before_execution() -> None:
    """Decorator flags are strict booleans and do no build-time work."""
    wire = Wire()
    with pytest.raises(ValueError, match='boolean'):
        depends_on_decision(
            client=native_client(wire),
            decisions=(DecisionStep('gate', questions={'one': request()}),),
            native_batch=cast('bool', 1),
        )
    assert not wire.bodies


def test_native_stage_old_single_provider_compatibility() -> None:
    """Single-only providers still execute sequentially inside an opted-in unit."""
    provider = Provider()
    plan = DecisionInvocationBuilder(Scope(), single_client(provider)).decide(
        'gate', questions={'one': request(), 'two': request()}, native_batch=True
    )
    assert plan.invoke('original').status == 'completed'
    assert provider.calls == len(('one', 'two'))


def test_structured_wrapper_native_forwarding() -> None:
    """Wrapper-first decisions retain native packets and validated structured output."""
    wire = Wire()
    scope = Scope()
    scope.response = scope.response.model_copy(update={'result': {'answer': 'ready'}})
    wrapper = StructuredOutputInvocationBuilder(scope, Answer)
    plan = wrapper.decide(
        'gate',
        client=native_client(wire),
        questions={'one': request(), 'two': request()},
        native_batch=True,
    )
    result = plan.invoke('original')
    assert result.response is not None
    assert isinstance(result.response.result, Answer)
    assert result.response.result.answer == 'ready'
    assert scope.calls[0][1]['structured_output'] is Answer
    assert len(wire.bodies) == 1
    assert len(wire.bodies[0]['questions']) == len(result.decisions['gate'])


def test_event_wrapper_native_forwarding() -> None:
    """Wrapper-first decisions retain native packets and existing event filters."""
    wire = Wire()
    scope = StreamingScope()
    observed: list[dict[str, object]] = []
    wrapper = EventInvocationBuilder(
        scope, include='session.completed', on_event=observed.append, auto_verbose=False
    )
    plan = wrapper.decide(
        'gate',
        client=native_client(wire),
        questions={'one': request(), 'two': request()},
        native_batch=True,
    )
    result = plan.invoke('original')
    assert result.response is not None
    assert result.response.response == 'done'
    assert len(observed) == 1
    assert observed[0]['category'] == 'session.completed'
    assert len(wire.bodies) == 1
    assert len(wire.bodies[0]['questions']) == len(result.decisions['gate'])
