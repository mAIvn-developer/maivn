"""Tests for invocation-local SDK tool execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Literal, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from maivn_contracts.tools import ToolOutcomeVariant

    from maivn._internal.reporting.terminal_reporter import BaseReporter

from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome, ToolCall
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field, field_validator

from maivn._internal import tool_runtime as tool_runtime_module
from maivn._internal.compat.decorators import (
    InterruptDependency,
    depends_on_agent,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_tool,
)
from maivn._internal.compat.tooling import compile_tool_metadata
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.reporting.context import current_reporter
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn._internal.wire import delegate_tools


@pytest.mark.parametrize(
    ('input_type', 'raw', 'expected'),
    [
        ('boolean', 'yes', True),
        ('boolean', ' no ', False),
        ('boolean', True, True),
        ('number', '12.5', 12.5),
        ('number', '2', 2),
        ('text', ' yes ', ' yes '),
        ('choice', 'blue', 'blue'),
    ],
)
def test_interrupt_collector_respects_declared_input_type(
    input_type: Literal['text', 'choice', 'boolean', 'number'],
    raw: object,
    expected: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal text must become a typed answer before crossing the canonical boundary."""
    monkeypatch.setattr(tool_runtime_module, 'get_current_reporter', lambda: None)
    dependency = InterruptDependency(
        arg_name='answer',
        input_handler=lambda _prompt: raw,
        input_type=input_type,
    )
    answer = asyncio.run(
        tool_runtime_module.collect_interrupt_value(dependency, tool_name='arbitrary')
    )
    assert answer == expected
    assert type(answer) is type(expected)


@pytest.mark.parametrize(
    ('input_type', 'raw'), [('boolean', 'perhaps'), ('number', 'NaN'), ('number', True)]
)
def test_interrupt_collector_rejects_invalid_typed_answers(
    input_type: Literal['boolean', 'number'],
    raw: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid input must not turn into consent or non-finite numeric data."""
    monkeypatch.setattr(tool_runtime_module, 'get_current_reporter', lambda: None)
    dependency = InterruptDependency(
        arg_name='answer',
        input_handler=lambda _prompt: raw,
        input_type=input_type,
    )
    with pytest.raises(ValueError, match='answer'):
        asyncio.run(tool_runtime_module.collect_interrupt_value(dependency, tool_name='arbitrary'))


class _RefusingAgent:
    """Fake delegate that fails the test if the client ever invokes it.

    The server runs the delegate as a nested session and binds its result before the tool
    call is published; the client's runtime must treat the agent as declaration only.
    """

    agent_id = 'data-analyzer'

    def __init__(self, tools: list[ToolMetadata] | None = None) -> None:
        """Optionally carry compiled tools, for delegate registration tests."""
        self._tools = list(tools or [])
        self.calls = 0

    def compile_tools(self) -> list[ToolMetadata]:
        """Return this delegate's own compiled tools."""
        return list(self._tools)

    async def ainvoke(self, _messages: object) -> object:
        """Fail: delegation is the server's decision now, not the SDK's."""
        self.calls += 1
        message = 'LocalToolRuntime must not invoke a delegate agent client-side'
        raise AssertionError(message)

    async def astream(self, _messages: object) -> object:
        """Fail: delegation is the server's decision now, not the SDK's."""
        self.calls += 1
        message = 'LocalToolRuntime must not stream a delegate agent client-side'
        raise AssertionError(message)


def test_sub_millisecond_tool_duration_is_reported_as_one_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive work must not become a misleading zero-duration Studio span."""
    ticks = iter((10.0, 10.0001))
    monkeypatch.setattr(tool_runtime_module.time, 'perf_counter', lambda: next(ticks))
    runtime = LocalToolRuntime([_tool('instant', lambda: 'done')], private_data=None)

    outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('instant')))

    assert isinstance(outcome, OkToolOutcome)
    assert outcome.duration_ms == 1


def test_domain_refusal_and_corrected_call_are_both_ok_status() -> None:
    """A callable that completes without raising is 'ok' even when its own result encodes failure.

    A tool that hands back a domain-level refusal - "no office named X" - as its
    return value, rather than raising, has still completed successfully as far as
    the runtime is concerned. Only an actual exception should produce an 'error'
    outcome; a corrected retry of the same tool must succeed the same way.
    """
    directory = {'Cedar Rapids': '52401'}

    def office_postal_code(office: str) -> dict[str, object]:
        if office not in directory:
            return {'error': f'no office named {office}', 'retryable': True}
        return {'office': office, 'postal_code': directory[office]}

    runtime = LocalToolRuntime([_tool('office_postal_code', office_postal_code)], private_data=None)

    async def invoke(office: str) -> ToolOutcomeVariant | None:
        return await runtime.outcome_for_event(
            _tool_start_event('office_postal_code', {'office': office})
        )

    failed = asyncio.run(invoke('Cedar Rapids office'))
    corrected = asyncio.run(invoke('Cedar Rapids'))

    assert isinstance(failed, OkToolOutcome)
    assert failed.result == {'error': 'no office named Cedar Rapids office', 'retryable': True}
    assert isinstance(corrected, OkToolOutcome)
    assert corrected.result == {'office': 'Cedar Rapids', 'postal_code': '52401'}


def test_agent_dependency_argument_arrives_server_bound_and_passes_through() -> None:
    """The delegated value is already in the tool_call arguments; the runtime keeps it.

    The server runs the delegate as a nested session and binds its result into the call
    before the tool_call event is published. The client's only job is to execute the tool
    with exactly the arguments the event carried - re-resolving the dependency here would
    run the delegate twice.
    """
    dependency_agent = _RefusingAgent()
    received: list[object] = []

    @depends_on_agent(dependency_agent, arg_name='analysis_result')
    def generate_report(analysis_result: object, dataset: str) -> dict[str, object]:
        received.append(analysis_result)
        return {'analysis_result': analysis_result, 'dataset': dataset}

    runtime = LocalToolRuntime([_tool('generate_report', generate_report)], private_data=None)

    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'generate_report',
                arguments={
                    'analysis_result': {'mean': 42.5, 'std': 12.3},
                    'dataset': 'customer_behavior',
                },
            )
        )
    )

    assert isinstance(outcome, OkToolOutcome)
    assert received == [{'mean': 42.5, 'std': 12.3}]
    assert outcome.result == {
        'analysis_result': {'mean': 42.5, 'std': 12.3},
        'dataset': 'customer_behavior',
    }
    # A completed operation always consumed positive time. Millisecond telemetry
    # must floor sub-millisecond work to 1ms instead of presenting a misleading 0ms
    # tool span in Studio's trace.
    assert outcome.duration_ms >= 1
    assert dependency_agent.calls == 0


def test_delegate_tools_collects_each_dependency_agents_compiled_tools() -> None:
    """Every delegate's own tools surface for local registration, deduplicated by name.

    The server hands the delegate its tool specs and the delegate's calls broker back over
    this client's stream - so the local runtime must know those callables by name, or the
    child's tool calls arrive for tools nobody registered.
    """

    def analyze_dataset(dataset: str) -> dict[str, object]:
        return {'dataset': dataset}

    analyzer_tool = _tool('analyze_dataset', analyze_dataset)
    dependency_agent = _RefusingAgent(tools=[analyzer_tool])

    @depends_on_agent(dependency_agent, arg_name='analysis_result')
    def generate_report(analysis_result: object) -> dict[str, object]:
        return {'analysis_result': analysis_result}

    report_tool = _tool('generate_report', generate_report)

    collected = delegate_tools([report_tool])

    assert [tool.name for tool in collected] == ['analyze_dataset']

    runtime = LocalToolRuntime([report_tool, *collected], private_data=None)
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('analyze_dataset', arguments={'dataset': 'customer_behavior'}),
        ),
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'dataset': 'customer_behavior'}


def test_tool_and_private_data_dependencies_retain_existing_injection_behavior() -> None:
    """Tool results and private data continue to resolve before local execution."""
    received: list[tuple[object, object]] = []

    def load_data() -> dict[str, int]:
        return {'records': 3}

    @depends_on_private_data(data_key='account_id', arg_name='account_id')
    @depends_on_tool(load_data, arg_name='data')
    def generate_report(data: object, account_id: object) -> dict[str, object]:
        received.append((data, account_id))
        return {'account_id': account_id, 'data': data}

    runtime = LocalToolRuntime(
        [_tool('load_data', load_data), _tool('generate_report', generate_report)],
        private_data={'account_id': 'acct-123'},
    )

    first_outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('load_data')))
    second_outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('generate_report')))

    assert isinstance(first_outcome, OkToolOutcome)
    assert isinstance(second_outcome, OkToolOutcome)
    assert received == [({'records': 3}, 'acct-123')]
    assert second_outcome.result == {'account_id': 'acct-123', 'data': {'records': 3}}


def test_nested_private_dependencies_override_hostile_values() -> None:
    """Decorated models receive private data in lists, mappings, tuples, and optional unions."""

    @depends_on_private_data(data_key='manufacturer', arg_name='manufacturer')
    class Motor(BaseModel):
        name: str
        manufacturer: str

    class Robot(BaseModel):
        motors: list[Motor]
        twins: list[Motor]
        by_role: dict[str, Motor]
        paired: tuple[Motor, Motor]
        backup: Motor | None

    runtime = LocalToolRuntime(
        [_tool('build_robot', Robot)],
        private_data={'manufacturer': 'Cote Robotics'},
    )
    hostile_motor = {'name': 'drive', 'manufacturer': 'TorquePro Robotics'}
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'build_robot',
                {
                    'motors': [hostile_motor],
                    'twins': [hostile_motor, hostile_motor],
                    'by_role': {'left': dict(hostile_motor)},
                    'paired': [
                        hostile_motor,
                        {'name': 'right', 'manufacturer': 'PrecisionDrive Inc.'},
                    ],
                    'backup': None,
                },
            )
        )
    )

    assert isinstance(outcome, OkToolOutcome)
    assert isinstance(outcome.result, dict)
    result = cast('dict[str, object]', outcome.result)
    assert result['backup'] is None
    motors = cast('list[dict[str, object]]', result['motors'])
    by_role = cast('dict[str, dict[str, object]]', result['by_role'])
    paired = cast('list[dict[str, object]]', result['paired'])
    assert motors[0]['manufacturer'] == 'Cote Robotics'
    twins = cast('list[dict[str, object]]', result['twins'])
    assert [motor['manufacturer'] for motor in twins] == ['Cote Robotics', 'Cote Robotics']
    assert by_role['left']['manufacturer'] == 'Cote Robotics'
    assert [motor['manufacturer'] for motor in paired] == [
        'Cote Robotics',
        'Cote Robotics',
    ]


def test_missing_nested_private_model_dependency_is_value_free_and_hidden_from_schema() -> None:
    """A missing nested key reports a value-free error and is never a provider input."""

    @depends_on_private_data(data_key='manufacturer', arg_name='manufacturer')
    class Motor(BaseModel):
        name: str
        manufacturer: str

    class Robot(BaseModel):
        motor: Motor

    tool = _tool('build_robot', Robot)
    runtime = LocalToolRuntime([tool], private_data={})
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'build_robot',
                {'motor': {'name': 'drive', 'manufacturer': 'TorquePro Robotics'}},
            )
        )
    )

    assert isinstance(outcome, ErrorToolOutcome)
    assert 'private_data_missing' in outcome.error.message
    assert 'TorquePro Robotics' not in outcome.error.message
    schema = compile_tool_metadata(tool).input_schema
    motor_definition = cast('dict[str, object]', schema['$defs']['Motor'])
    assert 'manufacturer' not in cast('dict[str, object]', motor_definition['properties'])
    assert 'manufacturer' not in cast('list[str]', motor_definition.get('required', []))


def test_nested_private_dependencies_support_aliases_discriminated_unions_and_recursion() -> None:
    """Private injection follows the supplied discriminated branch through recursive models."""

    @depends_on_private_data(data_key='manufacturer', arg_name='manufacturer')
    class ElectricMotor(BaseModel):
        kind: Literal['electric']
        battery: int
        manufacturer: str = Field(alias='maker')

    @depends_on_private_data(data_key='manufacturer', arg_name='manufacturer')
    class CombustionMotor(BaseModel):
        kind: Literal['combustion']
        octane: int
        manufacturer: str = Field(alias='maker')

    @depends_on_private_data(data_key='manufacturer', arg_name='manufacturer')
    class Assembly(BaseModel):
        name: str
        manufacturer: str = Field(alias='maker')
        child: Assembly | None = None

    class Vehicle(BaseModel):
        drive: Annotated[ElectricMotor | CombustionMotor, Field(discriminator='kind')]
        assembly: Assembly

    runtime = LocalToolRuntime(
        [_tool('build_vehicle', Vehicle)],
        private_data={'manufacturer': 'Cote Robotics'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'build_vehicle',
                {
                    'drive': {'kind': 'electric', 'battery': 86, 'maker': 'TorquePro'},
                    'assembly': {
                        'name': 'root',
                        'maker': 'PrecisionDrive',
                        'child': {'name': 'child', 'maker': 'TorquePro'},
                    },
                },
            )
        )
    )

    assert isinstance(outcome, OkToolOutcome)
    result = cast('dict[str, object]', outcome.result)
    drive = cast('dict[str, object]', result['drive'])
    assembly = cast('dict[str, object]', result['assembly'])
    child = cast('dict[str, object]', assembly['child'])
    assert drive['kind'] == 'electric'
    assert drive['maker'] == 'Cote Robotics'
    assert assembly['maker'] == 'Cote Robotics'
    assert child['maker'] == 'Cote Robotics'
    schema = compile_tool_metadata(_tool('build_vehicle', Vehicle)).input_schema
    definitions = cast('dict[str, dict[str, object]]', schema['$defs'])
    assert 'maker' not in cast('dict[str, object]', definitions['ElectricMotor']['properties'])
    assert 'maker' not in cast('dict[str, object]', definitions['Assembly']['properties'])


@pytest.mark.parametrize('branch', ['left', 'right'])
def test_equal_shape_union_injects_missing_private_fields_in_the_selected_branch(
    branch: str,
) -> None:
    """Discriminator values, not coincident field names, choose the developer's model."""

    @depends_on_private_data(data_key='left_value', arg_name='secret')
    class Left(BaseModel):
        kind: Literal['left']
        secret: str

    @depends_on_private_data(data_key='right_value', arg_name='secret')
    class Right(BaseModel):
        kind: Literal['right']
        secret: str

    class Envelope(BaseModel):
        payload: Annotated[Left | Right, Field(discriminator='kind')]

    runtime = LocalToolRuntime(
        [_tool('envelope', Envelope)],
        private_data={f'{branch}_value': f'{branch}-private'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'envelope',
                {'payload': {'kind': branch}},
            )
        )
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'payload': {'kind': branch, 'secret': f'{branch}-private'}}


def test_nested_validation_aliases_and_private_alias_paths_remain_hidden() -> None:
    """Aliases belong to Pydantic validation and do not expose caller-owned fields."""

    @depends_on_private_data(data_key='owner', arg_name='owner')
    class Item(BaseModel):
        owner: str = Field(validation_alias=AliasChoices('private_owner', AliasPath('keys', -1)))
        name: str

    class Envelope(BaseModel):
        model_config = ConfigDict(extra='forbid')
        item: Item = Field(validation_alias='nested_item')

    tool = _tool('envelope', Envelope)
    runtime = LocalToolRuntime([tool], private_data={'owner': 'actual'})
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'envelope',
                {'nested_item': {'name': 'ordinary', 'private_owner': 'hostile'}},
            )
        )
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'item': {'owner': 'actual', 'name': 'ordinary'}}
    schema = compile_tool_metadata(tool).input_schema
    assert 'private_owner' not in schema['$defs']['Item']['properties']


def test_typed_callable_keeps_model_identity_and_runs_field_validation() -> None:
    """A nested dependency yields the authored class and does not bypass validators."""
    seen: list[str] = []

    @depends_on_private_data(data_key='owner', arg_name='owner')
    class Item(BaseModel):
        owner: str = Field(alias='maker')

        @field_validator('owner')
        @classmethod
        def validate_owner(cls, value: str) -> str:
            seen.append(value)
            return value.upper()

    def accept(item: Item) -> str:
        assert type(item) is Item
        return item.owner

    # Local test classes need an explicit resolved annotation for get_type_hints.
    accept.__annotations__['item'] = Item
    runtime = LocalToolRuntime([_tool('accept', accept)], private_data={'owner': 'actual'})
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'accept',
                {'item': {'maker': 'hostile'}},
            )
        )
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == 'ACTUAL'
    assert seen == ['actual']


def test_fixed_tuple_arity_is_rejected_instead_of_truncated() -> None:
    """Private injection must not turn an invalid developer tuple into a valid one."""

    @depends_on_private_data(data_key='owner', arg_name='owner')
    class Item(BaseModel):
        owner: str

    class Pair(BaseModel):
        items: tuple[Item, Item]

    runtime = LocalToolRuntime([_tool('pair', Pair)], private_data={'owner': 'actual'})
    oversized_items: list[dict[str, object]] = [{}, {}, {}]
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'pair',
                {'items': oversized_items},
            )
        )
    )
    assert isinstance(outcome, ErrorToolOutcome)
    assert 'too_long' in outcome.error.message


def test_private_model_instances_keep_their_class_without_mutating_the_original() -> None:
    """Existing dependency results retain their class and serialization contract."""

    @depends_on_private_data(data_key='owner', arg_name='owner')
    class Item(BaseModel):
        owner: str = Field(alias='maker')

    def accept(item: Item) -> str:
        assert type(item) is Item
        return item.owner

    accept.__annotations__['item'] = Item
    original = Item(maker='original')
    runtime = LocalToolRuntime([_tool('accept', accept)], private_data={'owner': 'actual'})
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('accept', {'item': original}),
        )
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == 'actual'
    assert original.owner == 'original'


def test_private_alias_path_injection_preserves_public_sibling_data() -> None:
    """A private nested alias does not delete unrelated fields or leak into the schema."""

    @depends_on_private_data(data_key='owner', arg_name='owner')
    class Item(BaseModel):
        owner: str = Field(validation_alias=AliasPath('metadata', 'owners', -1))
        public_name: str = Field(validation_alias=AliasPath('metadata', 'name'))

    class Envelope(BaseModel):
        item: Item

    tool = _tool('envelope', Envelope)
    runtime = LocalToolRuntime([tool], private_data={'owner': 'actual'})
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event(
                'envelope',
                {'item': {'metadata': {'owners': ['hostile'], 'name': 'public'}}},
            )
        )
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'item': {'owner': 'actual', 'public_name': 'public'}}
    schema = compile_tool_metadata(tool).input_schema
    assert 'owner' not in schema['$defs']['Item']['properties']
    assert 'public_name' in schema['$defs']['Item']['properties']


def test_decorated_subclass_does_not_add_private_dependencies_to_its_parent() -> None:
    """Each developer class owns its dependency metadata and injected fields."""

    @depends_on_private_data(data_key='owner', arg_name='owner')
    class Parent(BaseModel):
        owner: str

    @depends_on_private_data(data_key='region', arg_name='region')
    class Child(Parent):
        region: str

    parent = LocalToolRuntime([_tool('parent', Parent)], private_data={'owner': 'parent'})
    outcome = asyncio.run(parent.outcome_for_event(_tool_start_event('parent', {})))
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'owner': 'parent'}

    child = LocalToolRuntime(
        [_tool('child', Child)], private_data={'owner': 'child', 'region': 'west'}
    )
    child_outcome = asyncio.run(child.outcome_for_event(_tool_start_event('child', {})))
    assert isinstance(child_outcome, OkToolOutcome)
    assert child_outcome.result == {'owner': 'child', 'region': 'west'}


def test_typed_callable_keeps_an_opaque_dependency_instance() -> None:
    """Non-Pydantic injected objects remain outside schema-based model validation."""

    class Client:
        pass

    client = Client()

    def accept(connection: Client) -> bool:
        return connection is client

    accept.__annotations__['connection'] = Client
    runtime = LocalToolRuntime([_tool('accept', accept)], private_data=None)
    outcome = asyncio.run(
        runtime.outcome_for_event(_tool_start_event('accept', {'connection': client}))
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result is True


def test_tool_dependency_waits_for_an_already_started_upstream_call() -> None:
    """One brokered graph phase may start dependents before their inputs finish."""
    upstream_started = asyncio.Event()
    release_upstream = asyncio.Event()

    async def load_data() -> dict[str, int]:
        upstream_started.set()
        await release_upstream.wait()
        return {'records': 3}

    @depends_on_tool(load_data, arg_name='data')
    def generate_report(data: object) -> dict[str, object]:
        return {'data': data}

    async def run_graph() -> tuple[object, object]:
        runtime = LocalToolRuntime(
            [_tool('load_data', load_data), _tool('generate_report', generate_report)],
            private_data=None,
        )
        upstream = asyncio.create_task(runtime.outcome_for_event(_tool_start_event('load_data')))
        await upstream_started.wait()
        downstream = asyncio.create_task(
            runtime.outcome_for_event(_tool_start_event('generate_report'))
        )
        await asyncio.sleep(0)
        assert not downstream.done()
        release_upstream.set()
        return await upstream, await downstream

    upstream_outcome, downstream_outcome = asyncio.run(run_graph())

    assert isinstance(upstream_outcome, OkToolOutcome)
    assert isinstance(downstream_outcome, OkToolOutcome)
    assert downstream_outcome.result == {'data': {'records': 3}}


def test_a_successful_retry_clears_the_failure_its_first_attempt_recorded() -> None:
    """A dependent must see the retry's result, not the stale first-attempt failure.

    The automobile demo regression (2026-08-26): a planned producer failed once,
    its retries all completed, and every downstream consumer still raised
    'tool dependency failed' - the success path filled results but never cleared
    the failure the first attempt recorded, and dependents check failures first.
    """
    attempts: list[int] = []

    def flaky_producer() -> dict[str, int]:
        attempts.append(1)
        if len(attempts) == 1:
            message = 'transient failure'
            raise RuntimeError(message)
        return {'records': 3}

    @depends_on_tool(flaky_producer, arg_name='data')
    def consumer(data: object) -> dict[str, object]:
        return {'data': data}

    async def run_graph() -> tuple[object, object, object]:
        runtime = LocalToolRuntime(
            [_tool('flaky_producer', flaky_producer), _tool('consumer', consumer)],
            private_data=None,
        )
        failed = await runtime.outcome_for_event(_tool_start_event('flaky_producer'))
        retried = await runtime.outcome_for_event(_tool_start_event('flaky_producer'))
        resolved = await runtime.outcome_for_event(_tool_start_event('consumer'))
        return failed, retried, resolved

    failed_outcome, retried_outcome, resolved_outcome = asyncio.run(run_graph())

    assert isinstance(failed_outcome, ErrorToolOutcome)
    assert isinstance(retried_outcome, OkToolOutcome)
    assert isinstance(resolved_outcome, OkToolOutcome)
    assert resolved_outcome.result == {'data': {'records': 3}}


def test_model_constructor_executes_without_thread_pool_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure Pydantic constructors stay on-loop instead of scheduling 144 threads."""

    class VehiclePart(BaseModel):
        name: str

    async def refuse_thread_dispatch(*_args: object, **_kwargs: object) -> object:
        message = 'model constructors must not use asyncio.to_thread'
        raise AssertionError(message)

    monkeypatch.setattr(asyncio, 'to_thread', refuse_thread_dispatch)
    runtime = LocalToolRuntime([_tool('VehiclePart', VehiclePart)], private_data=None)

    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('VehiclePart', arguments={'name': 'brake rotor'})
        )
    )

    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'name': 'brake rotor'}


def test_validation_failure_names_fields_without_echoing_inputs() -> None:
    """A structured-tool rejection says WHICH fields failed and echoes no input value.

    The complex-types Motor call (D-58-03) reached the trace as the bare string
    "Unknown error"; the diagnosable half of that fix is a value-free message
    minted here from validation locations and rule types alone.
    """

    class Motor(BaseModel):
        manufacturer: str
        max_power_w: float

    runtime = LocalToolRuntime([_tool('Motor', Motor)], private_data=None)

    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('Motor', arguments={'manufacturer': 'SECRET-INPUT-SENTINEL'})
        )
    )

    assert isinstance(outcome, ErrorToolOutcome)
    assert outcome.error.code == 'sdk_tool_validation_error'
    assert 'max_power_w' in outcome.error.message
    assert 'SECRET-INPUT-SENTINEL' not in outcome.error.message


@pytest.mark.parametrize('private_workspace', [False, True])
def test_overlap_recovery_is_fixed_text_in_both_custodies(*, private_workspace: bool) -> None:
    """Range identifiers in the exception never enter the recovery message."""

    class OverlapError(ValueError):
        sdk_error_code = 'sdk_workbook_range_overlap'

    class Workspace:
        def sdk_private_workspace(self, _arguments: object) -> bool:
            return private_workspace

        def place(self) -> None:
            message = 'SECRET-RANGE-SENTINEL overlaps SECRET-CELL-SENTINEL'
            raise OverlapError(message)

    runtime = LocalToolRuntime([_tool('place', Workspace().place)], private_data=None)
    outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('place')))

    assert isinstance(outcome, ErrorToolOutcome)
    assert outcome.error.code == 'sdk_workbook_range_overlap'
    assert outcome.error.retryable is True
    assert outcome.error.message == (
        'The new range overlaps an existing range. Read the workbook, '
        'update the existing range using its exact range_id and start_cell '
        'with a replacement composition, or choose unused cells.'
    )
    assert 'SECRET-' not in outcome.model_dump_json()


def test_private_composition_recovery_never_echoes_the_invalid_reference() -> None:
    """The known composition code is minted with static guidance only."""

    class CompositionError(ValueError):
        sdk_error_code = 'sdk_composition_reference_error'

    class Workspace:
        def sdk_private_workspace(self, _arguments: object) -> bool:
            return True

        def place(self) -> None:
            message = 'SECRET-REFERENCE-SENTINEL'
            raise CompositionError(message)

    runtime = LocalToolRuntime([_tool('place', Workspace().place)], private_data=None)
    outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('place')))

    assert isinstance(outcome, ErrorToolOutcome)
    assert outcome.error.code == 'sdk_composition_reference_error'
    assert outcome.error.message == (
        'Composition reference is unavailable in this workspace. '
        'Call compose_artifact for text/tables in this toolset and workspace, '
        'or compose_artifact_image for an attached image. '
        'Retry with the returned reference.'
    )
    assert 'SECRET-' not in outcome.model_dump_json()


def _tool(name: str, target: Callable[..., object]) -> ToolMetadata:
    """Build a local callable registration for one runtime test."""
    return ToolMetadata(name=name, target=target)


def test_private_invocation_keeps_later_handle_only_file_render_in_vault() -> None:
    """A render can use private content saved by an earlier composition call."""
    runtime = LocalToolRuntime(
        [_tool('render', lambda: None)], private_data={'customer': 'PRIVATE-CUSTOMER-SENTINEL'}
    )
    call = ToolCall.model_validate(
        _tool_start_event('render', {'document_id': 'opaque'}).payload['tool_call']
    )

    assert runtime.private_data_keys_for_call(call) == ('customer',)


def _tool_start_event(
    tool_name: str,
    arguments: Mapping[str, object] | None = None,
    *,
    call_id: str | None = None,
    lineage: Mapping[str, object] | None = None,
) -> StreamEvent:
    """Build the canonical payload consumed by one local SDK tool invocation."""
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': call_id or f'call-{tool_name}',
                    'spec_ref': {
                        'tool_id': tool_name,
                        'namespace': 'sdk',
                        'version': 'v1',
                    },
                    'arguments': dict(arguments or {}),
                    'lineage': lineage
                    or {
                        'session_id': 'ses-tool-runtime',
                        'invocation_id': 'inv-tool-runtime',
                    },
                }
            }
        },
    )


def _revision_lineage(
    *,
    root_call_id: str,
    parent_call_id: str,
    invocation_id: str = 'inv-tool-runtime',
) -> dict[str, str]:
    """Build canonical lineage for one SDK-local dependency revision."""
    return {
        'session_id': 'ses-tool-runtime',
        'invocation_id': invocation_id,
        'root_call_id': root_call_id,
        'parent_call_id': parent_call_id,
        'relation': 'dependency_revision',
    }


def test_dependency_revision_rebuilds_an_opted_nested_constructor_chain() -> None:
    """A validation-failed root may consume revised leaf and middle constructors."""

    class Leaf(BaseModel):
        value: int

    class Configuration(BaseModel):
        mode: str

    @depends_on_tool('leaf', 'leaf', revision_policy='on_validation_error')
    @depends_on_tool('configuration', 'configuration')
    class Middle(BaseModel):
        leaf: Leaf
        configuration: Configuration
        multiplier: int

    @depends_on_tool('middle', 'middle', revision_policy='on_validation_error')
    class Consumer(BaseModel):
        middle: Middle
        limit: int = Field(gt=10)

    runtime = LocalToolRuntime(
        [
            _tool('leaf', Leaf),
            _tool('configuration', Configuration),
            _tool('middle', Middle),
            _tool('consumer', Consumer),
        ],
        private_data=None,
    )

    async def scenario() -> tuple[object, object, object, object, object, object, object]:
        original_leaf = await runtime.outcome_for_event(
            _tool_start_event('leaf', {'value': 2}, call_id='leaf-base'),
        )
        configuration = await runtime.outcome_for_event(
            _tool_start_event('configuration', {'mode': 'safe'}, call_id='configuration-base'),
        )
        original_middle = await runtime.outcome_for_event(
            _tool_start_event('middle', {'multiplier': 3}, call_id='middle-base'),
        )
        failed = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 1}, call_id='consumer-base'),
        )
        revised_leaf = await runtime.outcome_for_event(
            _tool_start_event(
                'leaf',
                {'value': 7},
                call_id='leaf-revised',
                lineage=_revision_lineage(root_call_id='consumer-base', parent_call_id='leaf-base'),
            ),
        )
        revised_middle = await runtime.outcome_for_event(
            _tool_start_event(
                'middle',
                {'multiplier': 4},
                call_id='middle-revised',
                lineage=_revision_lineage(
                    root_call_id='consumer-base', parent_call_id='middle-base'
                ),
            ),
        )
        repaired = await runtime.outcome_for_event(
            _tool_start_event(
                'consumer',
                {'limit': 20},
                call_id='consumer-revised',
                lineage=_revision_lineage(
                    root_call_id='consumer-base', parent_call_id='consumer-base'
                ),
            ),
        )
        return (
            original_leaf,
            configuration,
            original_middle,
            failed,
            revised_leaf,
            revised_middle,
            repaired,
        )

    (
        original_leaf,
        configuration,
        original_middle,
        failed,
        revised_leaf,
        revised_middle,
        repaired,
    ) = asyncio.run(scenario())

    assert isinstance(original_leaf, OkToolOutcome)
    assert isinstance(configuration, OkToolOutcome)
    assert isinstance(original_middle, OkToolOutcome)
    assert isinstance(failed, ErrorToolOutcome)
    assert failed.error.code == 'sdk_tool_validation_error'
    assert isinstance(revised_leaf, OkToolOutcome)
    assert isinstance(revised_middle, OkToolOutcome)
    assert isinstance(repaired, OkToolOutcome)
    assert repaired.result == {
        'middle': {
            'leaf': {'value': 7},
            'configuration': {'mode': 'safe'},
            'multiplier': 4,
        },
        'limit': 20,
    }


def test_dependency_revision_keeps_base_and_sibling_roots_isolated() -> None:
    """One failed root's overlay cannot retarget another root or the base graph."""

    class Source(BaseModel):
        value: int

    @depends_on_tool('source', 'source', revision_policy='on_validation_error')
    class Consumer(BaseModel):
        source: Source
        limit: int = Field(gt=0)

    runtime = LocalToolRuntime(
        [_tool('source', Source), _tool('consumer', Consumer)], private_data=None
    )

    async def scenario() -> tuple[object, object, object, object, object, object, object]:
        source = await runtime.outcome_for_event(
            _tool_start_event('source', {'value': 1}, call_id='source-base'),
        )
        root_a = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 0}, call_id='consumer-a'),
        )
        root_b = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 0}, call_id='consumer-b'),
        )
        revised_source = await runtime.outcome_for_event(
            _tool_start_event(
                'source',
                {'value': 9},
                call_id='source-a',
                lineage=_revision_lineage(root_call_id='consumer-a', parent_call_id='source-base'),
            ),
        )
        revised_source_b = await runtime.outcome_for_event(
            _tool_start_event(
                'source',
                {'value': 5},
                call_id='source-b',
                lineage=_revision_lineage(root_call_id='consumer-b', parent_call_id='source-base'),
            ),
        )
        repaired_a = await runtime.outcome_for_event(
            _tool_start_event(
                'consumer',
                {'limit': 2},
                call_id='consumer-a-retry',
                lineage=_revision_lineage(root_call_id='consumer-a', parent_call_id='consumer-a'),
            ),
        )
        repaired_b = await runtime.outcome_for_event(
            _tool_start_event(
                'consumer',
                {'limit': 3},
                call_id='consumer-b-retry',
                lineage=_revision_lineage(root_call_id='consumer-b', parent_call_id='consumer-b'),
            ),
        )
        return source, root_a, root_b, revised_source, revised_source_b, repaired_a, repaired_b

    source, root_a, root_b, revised_source, revised_source_b, repaired_a, repaired_b = asyncio.run(
        scenario()
    )

    assert all(
        isinstance(item, OkToolOutcome)
        for item in (source, revised_source, revised_source_b, repaired_a, repaired_b)
    )
    assert isinstance(root_a, ErrorToolOutcome)
    assert isinstance(root_b, ErrorToolOutcome)
    assert isinstance(repaired_a, OkToolOutcome)
    assert isinstance(repaired_b, OkToolOutcome)
    assert repaired_a.result == {'source': {'value': 9}, 'limit': 2}
    assert repaired_b.result == {'source': {'value': 5}, 'limit': 3}


def test_dependency_revision_rejects_unopted_and_mismatched_lineage_before_execution() -> None:
    """Revision metadata cannot execute ordinary functions or cross invocation boundaries."""
    calls: list[int] = []

    class Source(BaseModel):
        value: int

    @depends_on_tool('source', 'source')
    class Consumer(BaseModel):
        source: Source
        limit: int = Field(gt=0)

    def ordinary() -> None:
        calls.append(1)

    runtime = LocalToolRuntime(
        [_tool('source', Source), _tool('consumer', Consumer), _tool('ordinary', ordinary)],
        private_data=None,
    )

    async def scenario() -> tuple[object, object, object, object]:
        source = await runtime.outcome_for_event(
            _tool_start_event('source', {'value': 1}, call_id='source')
        )
        failed = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 0}, call_id='consumer'),
        )
        unopted = await runtime.outcome_for_event(
            _tool_start_event(
                'source',
                {'value': 2},
                call_id='source-retry',
                lineage=_revision_lineage(root_call_id='consumer', parent_call_id='source'),
            ),
        )
        wrong_invocation = await runtime.outcome_for_event(
            _tool_start_event(
                'ordinary',
                call_id='ordinary-retry',
                lineage=_revision_lineage(
                    root_call_id='consumer',
                    parent_call_id='source',
                    invocation_id='other-invocation',
                ),
            ),
        )
        return source, failed, unopted, wrong_invocation

    source, failed, unopted, wrong_invocation = asyncio.run(scenario())

    assert isinstance(source, OkToolOutcome)
    assert isinstance(failed, ErrorToolOutcome)
    assert isinstance(unopted, ErrorToolOutcome)
    assert unopted.error.code == 'sdk_dependency_revision_not_opted_in'
    assert isinstance(wrong_invocation, ErrorToolOutcome)
    assert wrong_invocation.error.code == 'sdk_dependency_revision_invalid_lineage'
    assert calls == []


def test_ordinary_corrected_constructor_retry_remains_available_without_revision_lineage() -> None:
    """A direct model-argument correction remains an ordinary retry, not a repair branch."""

    class Source(BaseModel):
        value: int

    @depends_on_tool('source', 'source', revision_policy='on_validation_error')
    class Consumer(BaseModel):
        source: Source
        limit: int = Field(gt=0)

    runtime = LocalToolRuntime(
        [_tool('source', Source), _tool('consumer', Consumer)], private_data=None
    )

    async def scenario() -> tuple[object, object, object]:
        source = await runtime.outcome_for_event(_tool_start_event('source', {'value': 1}))
        failed = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 0}, call_id='consumer-failed'),
        )
        corrected = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 2}, call_id='consumer-corrected'),
        )
        return source, failed, corrected

    source, failed, corrected = asyncio.run(scenario())

    assert isinstance(source, OkToolOutcome)
    assert isinstance(failed, ErrorToolOutcome)
    assert failed.error.code == 'sdk_tool_validation_error'
    assert isinstance(corrected, OkToolOutcome)
    assert corrected.result == {'source': {'value': 1}, 'limit': 2}


def test_dependency_revision_allows_one_concurrent_generation_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent revisions sharing a parent cannot both replace one branch generation."""

    class Source(BaseModel):
        value: int

    @depends_on_tool('source', 'source', revision_policy='on_validation_error')
    class Consumer(BaseModel):
        source: Source
        limit: int = Field(gt=0)

    runtime = LocalToolRuntime(
        [_tool('source', Source), _tool('consumer', Consumer)], private_data=None
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original = runtime._call_target_in_order  # pyright: ignore[reportPrivateUsage] # noqa: SLF001

    async def delayed_call(
        tool: ToolMetadata,
        arguments: Mapping[str, object],
        assess_workspace: Callable[[], Awaitable[None]],
    ) -> object:
        entered.set()
        await release.wait()
        return await original(tool, arguments, assess_workspace)

    async def scenario() -> tuple[object, object]:
        await runtime.outcome_for_event(
            _tool_start_event('source', {'value': 1}, call_id='source-base')
        )
        failed = await runtime.outcome_for_event(
            _tool_start_event('consumer', {'limit': 0}, call_id='consumer-failed'),
        )
        assert isinstance(failed, ErrorToolOutcome)
        monkeypatch.setattr(runtime, '_call_target_in_order', delayed_call)
        first = asyncio.create_task(
            runtime.outcome_for_event(
                _tool_start_event(
                    'source',
                    {'value': 2},
                    call_id='source-first',
                    lineage=_revision_lineage(
                        root_call_id='consumer-failed', parent_call_id='source-base'
                    ),
                ),
            )
        )
        await entered.wait()
        second = await runtime.outcome_for_event(
            _tool_start_event(
                'source',
                {'value': 3},
                call_id='source-second',
                lineage=_revision_lineage(
                    root_call_id='consumer-failed', parent_call_id='source-base'
                ),
            ),
        )
        release.set()
        return await first, second

    first, second = asyncio.run(scenario())

    assert isinstance(first, OkToolOutcome)
    assert isinstance(second, ErrorToolOutcome)
    assert second.error.code == 'sdk_dependency_revision_conflict'


def test_dependency_revision_accepts_a_fully_opted_diamond_frontier() -> None:
    """One revised producer may feed multiple explicitly opted paths to the failed root."""

    class Leaf(BaseModel):
        value: int

    @depends_on_tool('leaf', 'leaf', revision_policy='on_validation_error')
    class Left(BaseModel):
        leaf: Leaf

    @depends_on_tool('leaf', 'leaf', revision_policy='on_validation_error')
    class Right(BaseModel):
        leaf: Leaf

    @depends_on_tool('left', 'left', revision_policy='on_validation_error')
    @depends_on_tool('right', 'right', revision_policy='on_validation_error')
    class Root(BaseModel):
        left: Left
        right: Right
        limit: int = Field(gt=0)

    runtime = LocalToolRuntime(
        [_tool('leaf', Leaf), _tool('left', Left), _tool('right', Right), _tool('root', Root)],
        private_data=None,
    )

    async def scenario() -> tuple[object, object]:
        await runtime.outcome_for_event(
            _tool_start_event('leaf', {'value': 1}, call_id='leaf-base')
        )
        await runtime.outcome_for_event(_tool_start_event('left', call_id='left-base'))
        await runtime.outcome_for_event(_tool_start_event('right', call_id='right-base'))
        failed = await runtime.outcome_for_event(
            _tool_start_event('root', {'limit': 0}, call_id='root-base'),
        )
        revised = await runtime.outcome_for_event(
            _tool_start_event(
                'leaf',
                {'value': 2},
                call_id='leaf-revised',
                lineage=_revision_lineage(root_call_id='root-base', parent_call_id='leaf-base'),
            ),
        )
        return failed, revised

    failed, revised = asyncio.run(scenario())

    assert isinstance(failed, ErrorToolOutcome)
    assert isinstance(revised, OkToolOutcome)


def test_interrupt_dependency_asks_the_human_and_hides_the_arg_from_the_model() -> None:
    """An interrupt argument is collected from the human, never invented by the model.

    The decorator has always recorded this dependency and nothing ever read it, and it was
    the one dependency kind still left in the model-facing schema. So the tool declared an
    argument only a human could supply, nobody asked the human, and the model kept trying to
    invent one until the loop ran out of iterations. That is what killed interrupt_demo.
    """
    asked: list[str] = []

    def handler(prompt: str) -> str:
        asked.append(prompt)
        return 'Ada'

    @depends_on_interrupt(arg_name='user_name', input_handler=handler, prompt='Your name? ')
    def greet(greeting_style: str, user_name: str) -> dict[str, str]:
        return {'style': greeting_style, 'name': user_name}

    runtime = LocalToolRuntime([_tool('greet', greet)], private_data=None)
    outcome = asyncio.run(
        runtime.outcome_for_event(_tool_start_event('greet', {'greeting_style': 'formal'})),
    )

    assert asked == ['Your name? ']
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'style': 'formal', 'name': 'Ada'}
    # and the model is never shown the argument it cannot supply
    schema = compile_tool_metadata(_tool('greet', greet)).input_schema
    properties = cast('dict[str, object]', schema.get('properties', {}))
    assert 'greeting_style' in properties
    assert 'user_name' not in properties


def test_canonical_interrupt_runtime_defers_incomplete_human_argument() -> None:
    """The canonical plane owns missing human arguments before local execution."""
    asked: list[str] = []

    @depends_on_interrupt(
        arg_name='user_name',
        input_handler=lambda prompt: asked.append(prompt) or 'Ada',
        prompt='Your name? ',
    )
    def greet(user_name: str) -> str:
        return f'Hello {user_name}'

    runtime = LocalToolRuntime(
        [_tool('greet', greet)],
        private_data=None,
        canonical_interrupts=True,
    )

    outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('greet')))

    assert outcome is None
    assert asked == []


def test_canonical_interrupt_runtime_executes_resumed_human_argument_once() -> None:
    """A resumed tool call uses its supplied answer without asking the handler again."""
    asked: list[str] = []

    @depends_on_interrupt(
        arg_name='user_name',
        input_handler=lambda prompt: asked.append(prompt) or 'wrong',
        prompt='Your name? ',
    )
    def greet(user_name: str) -> str:
        return f'Hello {user_name}'

    runtime = LocalToolRuntime(
        [_tool('greet', greet)],
        private_data=None,
        canonical_interrupts=True,
    )

    outcome = asyncio.run(
        runtime.outcome_for_event(_tool_start_event('greet', {'user_name': 'Ada'})),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == 'Hello Ada'
    assert asked == []


def test_interrupt_dependency_prefers_active_studio_reporter() -> None:
    """Route interactive input through Studio instead of the terminal when available."""
    reporter_calls: list[tuple[str, str, list[str] | None, str | None]] = []
    terminal_calls: list[str] = []

    class _StudioInputReporter:
        def get_input(  # noqa: PLR0913 - mirrors the reporter compatibility surface.
            self,
            prompt: str,
            *,
            input_type: str = 'text',
            choices: list[str] | None = None,
            data_key: str | None = None,
            arg_name: str | None = None,
            tool_name: str | None = None,
        ) -> str:
            reporter_calls.append((prompt, input_type, choices, data_key))
            assert arg_name == 'user_name'
            assert tool_name == 'greet_user'
            return 'Ada'

    def terminal_handler(prompt: str) -> str:
        terminal_calls.append(prompt)
        return 'terminal-value'

    @depends_on_interrupt(
        arg_name='user_name',
        prompt='Please enter your name: ',
        input_handler=terminal_handler,
    )
    def greet_user(user_name: str) -> dict[str, str]:
        return {'user_name': user_name}

    token = current_reporter.set(cast('BaseReporter', _StudioInputReporter()))
    try:
        runtime = LocalToolRuntime([_tool('greet_user', greet_user)], private_data=None)
        outcome = asyncio.run(runtime.outcome_for_event(_tool_start_event('greet_user')))
    finally:
        current_reporter.reset(token)

    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'user_name': 'Ada'}
    assert terminal_calls == []
    assert reporter_calls == [('Please enter your name: ', 'text', None, 'user_name')]


def test_a_private_data_placeholder_in_an_argument_is_resolved_before_execution() -> None:
    """A `{_{key}_}` placeholder must become the real value before the tool runs.

    The server never holds private values, so it hands the tool call over with the
    placeholder still in it and relies on this runtime - the developer's own process,
    which IS the vault boundary - to substitute. Nothing did, so an MCP fetch tool was
    handed the literal string `{_{alpha_url}_}` and rejected it as a malformed URL.
    That is what silently emptied every feed in financial-planner-mcp.
    """
    seen: dict[str, object] = {}

    def fetch(url: str) -> dict[str, str]:
        seen['url'] = url
        return {'status': 'ok'}

    runtime = LocalToolRuntime(
        [_tool('fetch', fetch)],
        private_data={'alpha_url': 'https://example.test/query?symbol=AAPL'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(_tool_start_event('fetch', {'url': '{_{alpha_url}_}'})),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['url'] == 'https://example.test/query?symbol=AAPL'


def test_a_caller_named_value_resolves_under_the_server_key() -> None:
    """A value the caller named `claim_id` comes back as `{_{user_claim_id}_}`.

    The server prefixes a caller-chosen name with `user_` so it cannot squat an
    auto-allocated slot, and hands the tool call back under that key. Booth supplied
    the claim id and its intake tool was still handed the literal
    `{_{user_claim_id}_}`, so the lookup missed and the run reported a $0 healthcare
    decision derived from nothing.
    """
    seen: dict[str, object] = {}

    def lookup(claim_id: str) -> dict[str, str]:
        seen['claim_id'] = claim_id
        return {'status': 'found'}

    runtime = LocalToolRuntime(
        [_tool('lookup', lookup)],
        private_data={'claim_id': 'CLM-2026-4401'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('lookup', {'claim_id': '{_{user_claim_id}_}'}),
        ),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['claim_id'] == 'CLM-2026-4401'


def test_an_exact_caller_key_wins_over_the_derived_one() -> None:
    """A caller whose own map really contains `user_claim_id` keeps that entry."""
    seen: dict[str, object] = {}

    def lookup(claim_id: str) -> dict[str, str]:
        seen['claim_id'] = claim_id
        return {'status': 'found'}

    runtime = LocalToolRuntime(
        [_tool('lookup', lookup)],
        private_data={'user_claim_id': 'EXACT', 'claim_id': 'DERIVED'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('lookup', {'claim_id': '{_{user_claim_id}_}'}),
        ),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['claim_id'] == 'EXACT'


def test_a_positional_server_key_stays_a_visible_placeholder() -> None:
    """`known_pii_1` is allocated by discovery order inside one server run.

    Nothing on this side supplied it, so guessing would turn a visible failure into
    a silent wrong answer.
    """
    seen: dict[str, object] = {}

    def lookup(claim_id: str) -> dict[str, str]:
        seen['claim_id'] = claim_id
        return {'status': 'found'}

    runtime = LocalToolRuntime(
        [_tool('lookup', lookup)],
        private_data={'claim_id': 'CLM-2026-4401'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('lookup', {'claim_id': '{_{known_pii_1}_}'}),
        ),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['claim_id'] == '{_{known_pii_1}_}'


def test_a_placeholder_is_resolved_inside_nested_arguments() -> None:
    """Placeholders travel inside lists and mappings, not only top-level strings."""
    seen: dict[str, object] = {}

    def fetch(spec: dict[str, object]) -> dict[str, str]:
        seen['spec'] = spec
        return {'status': 'ok'}

    runtime = LocalToolRuntime(
        [_tool('fetch', fetch)],
        private_data={'alpha_url': 'https://example.test/a'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _tool_start_event('fetch', {'spec': {'urls': ['{_{alpha_url}_}']}}),
        ),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['spec'] == {'urls': ['https://example.test/a']}


def test_a_placeholder_substitutes_within_surrounding_text() -> None:
    """The placeholder is a token inside a string, not necessarily the whole string."""
    seen: dict[str, object] = {}

    def fetch(url: str) -> dict[str, str]:
        seen['url'] = url
        return {'status': 'ok'}

    runtime = LocalToolRuntime(
        [_tool('fetch', fetch)],
        private_data={'host': 'example.test'},
    )
    outcome = asyncio.run(
        runtime.outcome_for_event(_tool_start_event('fetch', {'url': 'https://{_{host}_}/a'})),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['url'] == 'https://example.test/a'


def test_an_unregistered_placeholder_is_left_alone() -> None:
    """An unknown key is not ours to invent - leave it and let the tool report it.

    Substituting an empty string would turn a visible failure into a silent wrong
    answer, which is exactly the failure mode this whole path already suffered.
    """
    seen: dict[str, object] = {}

    def fetch(url: str) -> dict[str, str]:
        seen['url'] = url
        return {'status': 'ok'}

    runtime = LocalToolRuntime([_tool('fetch', fetch)], private_data={'known': 'x'})
    outcome = asyncio.run(
        runtime.outcome_for_event(_tool_start_event('fetch', {'url': '{_{missing}_}'})),
    )

    assert isinstance(outcome, OkToolOutcome)
    assert seen['url'] == '{_{missing}_}'


def test_a_repeat_call_does_not_replace_the_value_a_consumer_will_be_injected() -> None:
    """`_results` is what gets INJECTED, so a repeat must not retarget a bound edge.

    An agent may call one tool many times with different inputs. This map is keyed by
    tool name and was overwritten on every success, so calling a producer again before
    its consumer ran fed the consumer the LAST call's result instead of the planned one -
    silently, with no error anywhere. The server-side ledger cannot catch this: the
    injection happens here, in the developer's own process.
    """
    received: list[object] = []

    def search(query: str) -> dict[str, str]:
        return {'hit': query}

    @depends_on_tool(search, arg_name='hits')
    def report(hits: object) -> dict[str, object]:
        received.append(hits)
        return {'hits': hits}

    runtime = LocalToolRuntime(
        [_tool('search', search), _tool('report', report)],
        private_data=None,
    )

    async def scenario() -> None:
        await runtime.outcome_for_event(_tool_start_event('search', arguments={'query': 'first'}))
        # The extra invocation: same tool, different input, issued before the consumer.
        await runtime.outcome_for_event(_tool_start_event('search', arguments={'query': 'second'}))
        await runtime.outcome_for_event(_tool_start_event('report'))

    asyncio.run(scenario())

    assert received == [{'hit': 'first'}], 'the consumer was injected the repeat call'


def test_a_repeat_call_still_replaces_a_result_nothing_depends_on() -> None:
    """No edge is bound to it, so the newest answer is the right one to keep."""

    def lookup(term: str) -> dict[str, str]:
        return {'term': term}

    runtime = LocalToolRuntime([_tool('lookup', lookup)], private_data=None)

    async def scenario() -> None:
        await runtime.outcome_for_event(_tool_start_event('lookup', arguments={'term': 'first'}))
        await runtime.outcome_for_event(_tool_start_event('lookup', arguments={'term': 'second'}))

    asyncio.run(scenario())

    # The runtime's result map is the state this test exists to assert on.
    assert runtime._results['lookup'] == {  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        'term': 'second'
    }


def test_a_slow_planned_producer_still_wins_over_a_fast_repeat() -> None:
    """Arrival order decides the injected value, not completion order.

    A parallel model batch runs these concurrently, so "first result recorded wins" picks
    whichever call happens to FINISH first - and that can be the extra invocation. The
    sequential test cannot see this: it only ever has one call in flight.
    """
    received: list[object] = []

    async def search(query: str) -> dict[str, str]:
        # The planned call is the slow one, so completion order and arrival order differ.
        await asyncio.sleep(0.05 if query == 'first' else 0)
        return {'hit': query}

    @depends_on_tool(search, arg_name='hits')
    def report(hits: object) -> dict[str, object]:
        received.append(hits)
        return {'hits': hits}

    runtime = LocalToolRuntime(
        [_tool('search', search), _tool('report', report)],
        private_data=None,
    )

    async def scenario() -> None:
        planned = asyncio.create_task(
            runtime.outcome_for_event(_tool_start_event('search', arguments={'query': 'first'})),
        )
        # Arrives second, finishes first.
        await asyncio.sleep(0)
        extra = asyncio.create_task(
            runtime.outcome_for_event(_tool_start_event('search', arguments={'query': 'second'})),
        )
        await asyncio.gather(planned, extra)
        await runtime.outcome_for_event(_tool_start_event('report'))

    asyncio.run(scenario())

    assert received == [{'hit': 'first'}], 'completion order decided the injected value'
