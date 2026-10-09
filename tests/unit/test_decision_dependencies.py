# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
# Provider-isolated behavioral test fixtures.
# ruff: noqa: D101, D102, D103, D107, PLR2004
"""Provider-isolated managed decision dependency tests."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from functools import wraps
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from maivn._internal.compat.decorators import depends_on_private_data
from maivn._internal.decisions.dependencies import (
    DecisionResults,
    DecisionStep,
    decision_flow,
    depends_on_decision,
)

if TYPE_CHECKING:
    from maivn._internal.decisions.flow_models import DecisionContext
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
)


def run_async(function: Any) -> Any:
    @wraps(function)
    def run(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(function(*args, **kwargs))

    return run


def request() -> DecisionRequest:
    return DecisionRequest(
        name='check',
        state='public synthetic',
        instructions='Choose',
        choices=(
            DecisionChoice(key='yes', description='Run'),
            DecisionChoice(key='no', description='Skip'),
        ),
        privacy='public',
    )


class Provider:
    def __init__(self, value: str = 'yes', confidence: float = 0.95) -> None:
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.value = value
        self.confidence = confidence

    async def predict(self, question: DecisionRequest) -> DecisionPrediction:
        assert question.name
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.01)
            return DecisionPrediction(
                value=self.value, confidence=self.confidence, provider='fake', model='fake-model'
            )
        finally:
            self.active -= 1


def client(provider: Provider) -> DecisionClient:
    async def fallback(question: DecisionRequest) -> DecisionAnswer:
        assert question.name
        return DecisionAnswer(value='no', provider='baseline')

    return DecisionClient._from_backend(provider, fallback=fallback, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.


@run_async
async def test_lazy_injection_defaults_and_direct_call() -> None:
    provider = Provider()

    def target(value: int = 2, *, decisions: DecisionResults) -> object:
        assert decisions['allow']['answer'].value == 'yes'
        with pytest.raises(TypeError):
            decisions['allow']['new'] = decisions['allow']['answer']  # type: ignore[index]
        return value

    original = target
    target = depends_on_decision(
        client=client(provider),
        decisions=[DecisionStep('allow', questions={'answer': request()})],
        arg_name='decisions',
    )(target)
    assert target is original
    assert provider.calls == 0
    outcome = await decision_flow(target).ainvoke({})
    assert outcome.value == 2
    assert outcome.status == 'completed'
    with pytest.raises(ValueError, match='Caller'):
        await decision_flow(target).ainvoke({'decisions': {}})
    assert provider.calls == 1


@run_async
async def test_reversed_chain_join_and_context_isolation() -> None:
    provider = Provider()
    seen: dict[str, set[str]] = {}

    def factory(name: str) -> Callable[[DecisionContext], DecisionRequest]:
        def build(context: DecisionContext) -> DecisionRequest:
            seen[name] = set(context.decisions)
            assert isinstance(context.input, Mapping)
            assert cast('Mapping[str, object]', context.input)['value'] == 'private local'
            return request()

        return build

    steps = [
        DecisionStep('join', questions={'q': factory('join')}, after=('a', 'b')),
        DecisionStep('b', questions={'q': factory('b')}, after='a'),
        DecisionStep('independent', questions={'q': factory('independent')}),
        DecisionStep('a', questions={'q': factory('a')}),
    ]

    @depends_on_decision(client=client(provider), decisions=steps, max_concurrency=2)
    async def target(value: str) -> str:
        return value

    outcome = await decision_flow(target).ainvoke({'value': 'private local'})
    assert outcome.value == 'private local'
    assert seen == {'a': set(), 'b': {'a'}, 'join': {'a', 'b'}, 'independent': set()}
    assert provider.peak == 2


@pytest.mark.parametrize(
    'steps',
    [
        [DecisionStep('a', questions={'q': request()}, after='missing')],
        [
            DecisionStep('a', questions={'q': request()}, after='b'),
            DecisionStep('b', questions={'q': request()}, after='a'),
        ],
    ],
)
def test_invalid_graph_eager(steps: list[DecisionStep]) -> None:
    provider = Provider()
    with pytest.raises(ValueError, match='missing predecessors or a cycle'):
        depends_on_decision(client=client(provider), decisions=steps)
    assert provider.calls == 0


@run_async
@pytest.mark.parametrize(('confidence', 'status'), [(0.95, 'skipped'), (0.01, 'completed')])
async def test_primary_gate_and_fallback_no_gate(confidence: float, status: str) -> None:
    provider = Provider('no', confidence)
    calls: list[str] = []

    @depends_on_decision(
        client=client(provider),
        decisions=[
            DecisionStep('gate', questions={'q': request()}, require={'q': 'yes'}),
            DecisionStep('successor', questions={'q': request()}, after='gate'),
        ],
    )
    async def target() -> str:
        calls.append('called')
        return 'native'

    outcome = await decision_flow(target).ainvoke({})
    assert outcome.status == status
    assert provider.calls == (1 if status == 'skipped' else 2)
    assert calls == ([] if status == 'skipped' else ['called'])


@run_async
async def test_batch_order_shared_bound_and_cleanup() -> None:
    provider = Provider()

    @depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )
    async def target(value: int) -> int:
        await asyncio.sleep(0.001 * (5 - value))
        return value

    results = await decision_flow(target).abatch(
        [{'value': value} for value in range(5)], max_concurrency=2
    )
    assert [result.value for result in results] == list(range(5))
    assert provider.peak == 2
    assert provider.active == 0
    task = asyncio.create_task(  # noqa: RUF006 - task retained and cancelled below.
        decision_flow(target).abatch([{'value': 1} for _ in range(10)], max_concurrency=2)
    )
    await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.active == 0


def test_bad_injection_annotation_and_missing_input() -> None:
    provider = Provider()

    def target(decisions: object) -> None:
        pass

    with pytest.raises(TypeError, match='DecisionResults'):
        depends_on_decision(
            client=client(provider),
            decisions=[DecisionStep('a', questions={'q': request()})],
            arg_name='decisions',
        )(target)

    @depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )
    def required(value: int) -> int:
        return value

    with pytest.raises(TypeError):
        decision_flow(required).invoke({})
    assert provider.calls == 0


@run_async
async def test_invalid_later_batch_item_does_not_start_provider() -> None:
    provider = Provider()

    @depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )
    async def target(value: int) -> int:
        return value

    with pytest.raises(TypeError):
        await decision_flow(target).abatch([{'value': 1}, {}])
    await asyncio.sleep(0)
    assert provider.calls == 0


@run_async
async def test_group_bound_applies_to_questions_when_batch_bound_is_larger() -> None:
    provider = Provider()

    @depends_on_decision(
        client=client(provider),
        max_concurrency=1,
        decisions=[
            DecisionStep('a', questions={'q1': request(), 'q2': request()}),
            DecisionStep('b', questions={'q1': request(), 'q2': request()}),
        ],
    )
    async def target() -> int:
        return 7

    result = await decision_flow(target).abatch([{}], max_concurrency=8)
    assert result[0].value == 7
    assert provider.peak == 1


@run_async
async def test_client_override_does_not_leak_to_next_step() -> None:
    default = Provider()
    alternate = Provider()

    @depends_on_decision(
        client=client(default),
        decisions=[
            DecisionStep('a', client=client(alternate), questions={'q': request()}),
            DecisionStep('b', questions={'q': request()}, after='a'),
        ],
    )
    async def target() -> None:
        pass

    await decision_flow(target).ainvoke({})
    assert default.calls == alternate.calls == 1


@run_async
async def test_sync_worker_is_drained_even_if_it_fails_after_cancel() -> None:

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    provider = Provider()

    @depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )
    def target() -> None:
        started.set()
        release.wait()
        try:
            message = 'synthetic worker failure'
            raise RuntimeError(message)
        finally:
            finished.set()

    task = asyncio.create_task(decision_flow(target).ainvoke({}))
    await asyncio.to_thread(started.wait)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


@run_async
async def test_fallback_error_propagates_without_target_call() -> None:
    provider = Provider(confidence=0.01)

    async def fallback(question: DecisionRequest) -> DecisionAnswer:
        assert question.name
        message = 'fallback failed'
        raise RuntimeError(message)

    decision_client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        provider, fallback=fallback, config=DecisionConfig()
    )

    @depends_on_decision(
        client=decision_client, decisions=[DecisionStep('a', questions={'q': request()})]
    )
    async def target() -> None:
        pytest.fail('target should not execute')

    with pytest.raises(RuntimeError, match='fallback failed'):
        await decision_flow(target).ainvoke({})
    assert provider.active == 0


def test_class_target_rejected_before_provider() -> None:
    provider = Provider()
    decorate = depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )

    class Model:
        pass

    with pytest.raises(TypeError, match='ordinary function'):
        decorate(Model)
    assert provider.calls == 0


def test_invalid_required_choice_eager() -> None:
    provider = Provider()
    with pytest.raises(ValueError, match='declared choice'):
        depends_on_decision(
            client=client(provider),
            decisions=[DecisionStep('a', questions={'q': request()}, require={'q': 'invalid'})],
        )
    assert provider.calls == 0


@depends_on_private_data(data_key='secret', arg_name='secret')
class PrivateInput(BaseModel):
    public: str
    secret: str


@run_async
async def test_private_model_missing_context_blocks_provider() -> None:
    provider = Provider()

    @depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )
    async def target(value: PrivateInput) -> str:
        return value.public

    with pytest.raises(ValidationError, match='private_data_missing'):
        await decision_flow(target).ainvoke({'value': {'public': 'synthetic', 'secret': 'forged'}})
    assert provider.calls == 0


def test_private_injection_collision_eager() -> None:
    provider = Provider()

    @depends_on_private_data(data_key='private', arg_name='decisions')
    def target(*, decisions: DecisionResults) -> object:
        return decisions

    with pytest.raises(ValueError, match='collides'):
        depends_on_decision(
            client=client(provider),
            decisions=[DecisionStep('a', questions={'q': request()})],
            arg_name='decisions',
        )(target)
    assert provider.calls == 0


@run_async
async def test_static_step_preserves_opaque_native_arguments() -> None:
    provider = Provider()
    lock = threading.Lock()

    @depends_on_decision(
        client=client(provider), decisions=[DecisionStep('a', questions={'q': request()})]
    )
    async def target(native: object) -> object:
        return native

    result = await decision_flow(target).ainvoke({'native': lock})
    assert result.value is lock
    assert provider.calls == 1


@run_async
async def test_state_selector_narrows_factory_context_once_per_stage() -> None:
    provider = Provider()
    selected: list[Mapping[str, object]] = []
    observed: list[object] = []

    def select(bound: Mapping[str, object]) -> object:
        selected.append(bound)
        with pytest.raises(TypeError):
            cast('dict[str, object]', bound)['value'] = 'changed'
        return {'value': bound['value']}

    def factory(context: DecisionContext) -> DecisionRequest:
        snapshot = cast('Mapping[str, object]', context.input)
        assert set(snapshot) == {'value'}
        with pytest.raises(TypeError):
            cast('dict[str, object]', snapshot)['value'] = 'changed'
        observed.append(snapshot['value'])
        return request()

    lock = threading.Lock()

    @depends_on_decision(
        client=client(provider),
        state=select,
        decisions=[
            DecisionStep('a', questions={'first': factory, 'second': factory}),
            DecisionStep('static', questions={'q': request()}),
        ],
    )
    async def target(native: object, value: str = 'local default') -> object:
        assert value == 'local default'
        return native

    assert not selected
    assert provider.calls == 0
    result = await decision_flow(target).ainvoke({'native': lock})
    assert result.value is lock
    assert len(selected) == 1
    assert selected[0]['native'] is lock
    assert observed == ['local default', 'local default']


def test_async_state_selector_rejected_eagerly() -> None:
    provider = Provider()

    async def select(bound: Mapping[str, object]) -> object:
        return bound

    with pytest.raises(TypeError, match='synchronous'):
        depends_on_decision(
            client=client(provider),
            state=select,
            decisions=[DecisionStep('a', questions={'q': request()})],
        )
    assert provider.calls == 0
