# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Decision gates use actual tool registration and local runtime boundaries."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import httpx
import pytest
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome
from pydantic import BaseModel, Field

from maivn import Agent, Client, depends_on_decision, depends_on_private_data, depends_on_tool
from maivn._internal.models import StreamEvent
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
    DecisionResults,
    DecisionStep,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


class Payload(BaseModel):
    """A native model argument whose validator must precede decision inference."""

    count: int = Field(gt=0)


class Provider:
    """Return controlled primary results without networking."""

    def __init__(self, value: str = 'yes', *, unavailable: bool = False) -> None:
        """Store the controlled provider policy."""
        self.value = value
        self.unavailable = unavailable
        self.calls = 0
        self.fallbacks = 0

    async def predict(self, request: DecisionRequest) -> DecisionPrediction:
        """Record primary calls and optionally exercise fallback."""
        assert request.privacy == 'public'
        self.calls += 1
        if self.unavailable:
            message = 'Synthetic outage'
            raise RuntimeError(message)
        return DecisionPrediction(
            value=self.value, provider='fake', model='fake-model', confidence=0.99
        )

    async def fallback(self, request: DecisionRequest) -> DecisionAnswer:
        """Preserve baseline execution even when fallback returns a negative value."""
        assert request.name
        self.fallbacks += 1
        return DecisionAnswer(value='no', provider='baseline')

    def client(self) -> DecisionClient:
        """Create an explicit mandatory-fallback client."""
        return DecisionClient._from_backend(self, fallback=self.fallback, config=DecisionConfig())  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.


def request() -> DecisionRequest:
    """Build a content-fixed public question."""
    return DecisionRequest(
        name='process',
        state='Synthetic public input',
        instructions='Choose',
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='Run'),
            DecisionChoice(key='no', description='Skip'),
        ),
    )


def event(name: str, arguments: Mapping[str, object] | None = None) -> StreamEvent:
    """Build a canonical local tool-start event."""
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': f'call-{name}',
                    'spec_ref': {'tool_id': name, 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': dict(arguments or {}),
                    'lineage': {'session_id': 'ses-decision', 'invocation_id': 'inv-decision'},
                }
            }
        },
    )


def agent() -> Agent:
    """Build explicit offline scope state without loading application credentials."""

    def refuse(request: httpx.Request) -> httpx.Response:
        message = f'Unexpected platform request: {request.url.path}'
        raise AssertionError(message)

    return Agent(
        name='Decision tool proof',
        client=Client(
            api_key='offline',
            base_url='http://offline.invalid',
            transport=httpx.MockTransport(refuse),
            private_data_store=None,
        ),
    )


@pytest.mark.parametrize('outer', [True, False])
@pytest.mark.parametrize('value', ['yes', 'no'])
def test_toolify_orders_schema_and_actual_execution(*, outer: bool, value: str) -> None:
    """Both stacked decorator orders gate the registered target and hide injected results."""
    provider = Provider(value)
    scope = agent()
    seen: list[str] = []

    async def process(message: str, decisions: DecisionResults) -> str:
        seen.append(message)
        assert decisions['gate']['process'].value == 'yes'
        return 'native-result'

    decorator = depends_on_decision(
        client=provider.client(),
        decisions=[
            DecisionStep('gate', questions={'process': request()}, require={'process': 'yes'})
        ],
        arg_name='decisions',
    )
    process = decorator(scope.toolify()(process)) if outer else scope.toolify()(decorator(process))
    compiled = scope.compile_tools()
    assert compiled[0].target is process
    assert compiled[0].input_schema['properties'] == {'message': {'type': 'string'}}
    assert provider.calls == 0
    runtime = LocalToolRuntime(compiled, private_data=None)
    result = asyncio.run(runtime.outcome_for_event(event('process', {'message': 'Synthetic'})))
    assert isinstance(result, OkToolOutcome)
    if value == 'no':
        assert result.result == {'status': 'skipped', 'skipped_by': 'gate'}
        assert seen == []
    else:
        assert result.result == 'native-result'
        assert seen == ['Synthetic']


def test_supplied_injection_is_refused_before_provider() -> None:
    """A hidden argument cannot be forged through a raw tool call."""
    provider = Provider()
    scope = agent()

    @scope.toolify()
    @depends_on_decision(
        client=provider.client(),
        decisions=[DecisionStep('gate', questions={'process': request()})],
        arg_name='decisions',
    )
    async def process(message: str, decisions: DecisionResults) -> str:
        assert decisions
        return message

    assert scope.compile_tools()[0].target is process
    runtime = LocalToolRuntime(scope.compile_tools(), private_data=None)
    result = asyncio.run(
        runtime.outcome_for_event(
            event('process', {'message': 'Synthetic', 'decisions': {'forged': True}})
        )
    )
    assert isinstance(result, ErrorToolOutcome)
    assert provider.calls == 0


def test_invalid_native_model_precedes_primary() -> None:
    """The gate cannot bypass existing Pydantic tool argument validation."""
    provider = Provider()
    scope = agent()

    @scope.toolify()
    @depends_on_decision(
        client=provider.client(), decisions=[DecisionStep('gate', questions={'process': request()})]
    )
    async def process(payload: Payload) -> int:
        return payload.count

    assert scope.compile_tools()[0].target is process
    runtime = LocalToolRuntime(scope.compile_tools(), private_data=None)
    result = asyncio.run(runtime.outcome_for_event(event('process', {'payload': {'count': 0}})))
    assert isinstance(result, ErrorToolOutcome)
    assert provider.calls == 0
    assert result.error.code == 'sdk_tool_validation_error'


def test_negative_fallback_preserves_native_tool() -> None:
    """An unavailable optional model preserves the current execution path."""
    provider = Provider(unavailable=True)
    scope = agent()

    @scope.toolify()
    @depends_on_decision(
        client=provider.client(),
        decisions=[
            DecisionStep('gate', questions={'process': request()}, require={'process': 'yes'})
        ],
    )
    def process(message: str) -> str:
        return message

    assert scope.compile_tools()[0].target is process
    runtime = LocalToolRuntime(scope.compile_tools(), private_data=None)
    result = asyncio.run(runtime.outcome_for_event(event('process', {'message': 'Synthetic'})))
    assert isinstance(result, OkToolOutcome)
    assert result.result == 'Synthetic'
    assert provider.fallbacks == 1


def test_skipped_producer_cannot_supply_dependency() -> None:
    """A skip marker is never accepted as the producer's ordinary output."""
    provider = Provider('no')
    scope = agent()
    consumed: list[object] = []

    @scope.toolify()
    @depends_on_decision(
        client=provider.client(),
        decisions=[
            DecisionStep('gate', questions={'process': request()}, require={'process': 'yes'})
        ],
    )
    async def producer() -> str:
        message = 'Skipped producer must not run'
        raise AssertionError(message)

    @scope.toolify()
    @depends_on_tool(producer, 'value')
    async def consumer(value: str) -> str:
        consumed.append(value)
        return value

    assert scope.compile_tools()[-1].target is consumer
    runtime = LocalToolRuntime(scope.compile_tools(), private_data=None)

    async def run() -> None:
        skipped = await runtime.outcome_for_event(event('producer'))
        assert isinstance(skipped, OkToolOutcome)
        failed = await runtime.outcome_for_event(event('consumer'))
        assert isinstance(failed, ErrorToolOutcome)

    asyncio.run(run())
    assert consumed == []


def test_static_gate_accepts_opaque_private_dependency() -> None:
    """Keep irrelevant opaque dependencies local without copying or exporting them."""
    provider = Provider()
    scope = agent()
    local_lock = threading.Lock()

    @scope.toolify()
    @depends_on_private_data('lock', 'lock')
    @depends_on_decision(
        client=provider.client(), decisions=[DecisionStep('gate', questions={'process': request()})]
    )
    async def process(lock: object) -> str:
        assert lock is local_lock
        return 'done'

    assert scope.compile_tools()[0].target is process
    runtime = LocalToolRuntime(scope.compile_tools(), private_data={'lock': local_lock})
    result = asyncio.run(runtime.outcome_for_event(event('process')))
    assert isinstance(result, OkToolOutcome)
    assert result.result == 'done'
    assert provider.calls == 1


def test_cancelled_guarded_sync_tool_drains_worker() -> None:
    """Retain ownership until a guarded sync tool exits despite repeated cancellation."""
    provider = Provider()
    scope = agent()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    @scope.toolify()
    @depends_on_decision(
        client=provider.client(), decisions=[DecisionStep('gate', questions={'process': request()})]
    )
    def process() -> str:
        started.set()
        try:
            assert release.wait(3)
            return 'done'
        finally:
            finished.set()

    assert scope.compile_tools()[0].target is process
    runtime = LocalToolRuntime(scope.compile_tools(), private_data=None)

    async def run() -> None:
        task = asyncio.create_task(runtime.outcome_for_event(event('process')))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())
