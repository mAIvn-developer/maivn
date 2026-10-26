"""Characterize LocalToolRuntime event filtering, failures, revisions and serialized lanes."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import pytest
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome, ToolCall
from pydantic import BaseModel, Field

from maivn import depends_on_interrupt, depends_on_private_data, depends_on_tool
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from maivn_contracts.tools import ToolOutcomeVariant


def _tool(
    name: str, target: Callable[..., object], *, metadata: dict[str, object] | None = None
) -> ToolMetadata:
    return ToolMetadata(name=name, target=target, metadata=metadata or {})


def _call_payload(
    tool_id: str,
    arguments: Mapping[str, object] | None = None,
    *,
    call_id: str | None = None,
    namespace: str = 'sdk',
    lineage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        'call_id': call_id or f'call-{tool_id}',
        'spec_ref': {'tool_id': tool_id, 'namespace': namespace, 'version': 'v1'},
        'arguments': dict(arguments or {}),
        'lineage': dict(lineage or {'session_id': 'ses-rt', 'invocation_id': 'inv-rt'}),
    }


def _start(
    tool_id: str,
    arguments: Mapping[str, object] | None = None,
    *,
    call_id: str | None = None,
    namespace: str = 'sdk',
    lineage: Mapping[str, object] | None = None,
) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': _call_payload(
                    tool_id, arguments, call_id=call_id, namespace=namespace, lineage=lineage
                )
            }
        },
    )


def _revision(root_call_id: str | None, parent_call_id: str | None) -> dict[str, object]:
    lineage: dict[str, object] = {
        'session_id': 'ses-rt',
        'invocation_id': 'inv-rt',
        'relation': 'dependency_revision',
    }
    if root_call_id is not None:
        lineage['root_call_id'] = root_call_id
    if parent_call_id is not None:
        lineage['parent_call_id'] = parent_call_id
    return lineage


def _run(runtime: LocalToolRuntime, event: StreamEvent) -> ToolOutcomeVariant | None:
    return asyncio.run(runtime.outcome_for_event(event))


def _error(outcome: ToolOutcomeVariant | None) -> tuple[str, str]:
    assert isinstance(outcome, ErrorToolOutcome)
    return outcome.error.code, outcome.error.message


# --- event filtering and lookups ---------------------------------------------------


def test_non_actionable_events_return_no_outcome() -> None:
    """Other event types, malformed calls and foreign namespaces are ignored."""
    calls: list[str] = []

    def echo() -> str:
        calls.append('echo')
        return 'x'

    runtime = LocalToolRuntime([_tool('echo', echo)], private_data=None)
    completion = StreamEvent(
        position=1,
        event_type='system_tool_complete',
        data={'payload': {'tool_call': _call_payload('echo')}},
    )
    malformed = StreamEvent(
        position=1, event_type='system_tool_start', data={'payload': {'tool_call': 'echo'}}
    )
    assert _run(runtime, completion) is None
    assert _run(runtime, malformed) is None
    assert _run(runtime, _start('echo', namespace='hosted')) is None
    assert calls == []


def test_unknown_sdk_tool_is_an_error_outcome_reported_to_the_callback() -> None:
    """An unregistered SDK tool returns unknown_sdk_tool and completes the callback."""
    completed: list[tuple[str, str]] = []
    runtime = LocalToolRuntime([], private_data=None)
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _start('missing'),
            on_completed=lambda call, result: completed.append((call.call_id, result.status)),
        )
    )
    assert _error(outcome) == ('unknown_sdk_tool', 'SDK tool is not registered: missing')
    assert completed == [('call-missing', 'error')]


def test_lookups_ignore_foreign_namespaces_and_unknown_tools() -> None:
    """Metadata and custody keys exist only for registered SDK tools."""
    runtime = LocalToolRuntime([_tool('echo', lambda: 'x')], private_data={'k': 'v'})
    foreign = ToolCall.model_validate(_call_payload('echo', namespace='hosted'))
    unknown = ToolCall.model_validate(_call_payload('nope'))
    assert runtime.metadata_for_call(foreign) is None
    assert runtime.private_data_keys_for_call(unknown) == ()
    assert runtime.take_generated_file_sources(unknown) == ()


# --- execution failures and targets ------------------------------------------------


def test_failing_tool_reports_start_hook_order_and_completion() -> None:
    """A raising tool reports start then error completion with its exception text."""
    seen: list[str] = []

    def boom() -> None:
        message = 'tool exploded'
        raise RuntimeError(message)

    runtime = LocalToolRuntime([_tool('boom', boom)], private_data=None)
    outcome = asyncio.run(
        runtime.outcome_for_event(
            _start('boom'),
            on_started=lambda call: seen.append(f'start:{call.call_id}'),
            on_completed=lambda call, result: seen.append(f'done:{call.call_id}:{result.status}'),
        )
    )
    assert _error(outcome) == ('sdk_tool_error', 'tool exploded')
    assert seen == ['start:call-boom', 'done:call-boom:error']


def test_exception_without_text_reports_its_type_name() -> None:
    """An empty exception message falls back to the exception class name."""

    def silent() -> None:
        raise LookupError

    runtime = LocalToolRuntime([_tool('silent', silent)], private_data=None)
    assert _error(_run(runtime, _start('silent'))) == ('sdk_tool_error', 'LookupError')


def test_missing_private_data_key_fails_before_the_tool_runs() -> None:
    """A declared private dependency absent from private data is a KeyError outcome."""
    called: list[object] = []

    @depends_on_private_data('account_number', 'account')
    def lookup(account: str) -> str:
        called.append(account)
        return account

    runtime = LocalToolRuntime([_tool('lookup', lookup)], private_data={'other': 'x'})
    assert _error(_run(runtime, _start('lookup'))) == (
        'sdk_tool_error',
        '"private data is missing key \'account_number\'"',
    )
    assert called == []


def test_consumer_without_a_completed_producer_fails() -> None:
    """A dependency whose producer never ran is reported as not completed."""

    def producer() -> str:
        return 'value'

    @depends_on_tool('producer', 'value')
    def consumer(value: str) -> str:
        return value

    runtime = LocalToolRuntime(
        [_tool('producer', producer), _tool('consumer', consumer)], private_data=None
    )
    assert _error(_run(runtime, _start('consumer'))) == (
        'sdk_tool_error',
        'tool dependency has not completed: producer',
    )


def test_consumer_of_a_failed_producer_reports_the_failure() -> None:
    """A failed producer poisons its consumer with the producer's message."""

    def producer() -> str:
        message = 'upstream broke'
        raise RuntimeError(message)

    @depends_on_tool('producer', 'value')
    def consumer(value: str) -> str:
        return value

    runtime = LocalToolRuntime(
        [_tool('producer', producer), _tool('consumer', consumer)], private_data=None
    )

    async def scenario() -> tuple[ToolOutcomeVariant | None, ToolOutcomeVariant | None]:
        first = await runtime.outcome_for_event(_start('producer'))
        second = await runtime.outcome_for_event(_start('consumer'))
        return first, second

    first, second = asyncio.run(scenario())
    assert _error(first) == ('sdk_tool_error', 'upstream broke')
    assert _error(second) == (
        'sdk_tool_error',
        'tool dependency failed: producer: upstream broke',
    )


def test_unresolvable_annotations_still_call_the_target() -> None:
    """Type hints that cannot be resolved skip private model validation, not the call."""

    def echo(value: object) -> object:
        return {'echo': value}

    echo.__annotations__ = {'value': 'UndefinedType', 'return': 'object'}
    runtime = LocalToolRuntime([_tool('echo', echo)], private_data=None)
    outcome = _run(runtime, _start('echo', {'value': [1, 2]}))
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'echo': [1, 2]}


def test_sync_target_returning_an_awaitable_is_awaited() -> None:
    """A synchronous callable that returns a coroutine yields the awaited value."""

    async def later() -> str:
        return 'awaited'

    def deferred() -> object:
        return later()

    runtime = LocalToolRuntime([_tool('deferred', deferred)], private_data=None)
    outcome = _run(runtime, _start('deferred'))
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == 'awaited'


# --- interrupts --------------------------------------------------------------------


def test_interrupt_choices_extend_the_prompt_and_async_handlers_are_awaited() -> None:
    """Choices are appended to the prompt; an async handler's answer is awaited."""
    prompts: list[str] = []

    async def handler(prompt: str) -> str:
        prompts.append(prompt)
        return 'blue'

    @depends_on_interrupt(
        arg_name='color', input_handler=handler, prompt='Pick: ', choices=['red', 'blue']
    )
    def paint(color: str) -> str:
        return f'painted {color}'

    runtime = LocalToolRuntime([_tool('paint', paint)], private_data=None)
    outcome = _run(runtime, _start('paint'))
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == 'painted blue'
    assert prompts == ["Pick: ['red', 'blue'] "]


def test_interrupt_without_prompt_uses_the_argument_name() -> None:
    """An empty prompt defaults to '<arg_name>: '."""
    prompts: list[str] = []

    def handler(prompt: str) -> str:
        prompts.append(prompt)
        return 'Ada'

    @depends_on_interrupt(arg_name='name', input_handler=handler)
    def greet(name: str) -> str:
        return f'hi {name}'

    runtime = LocalToolRuntime([_tool('greet', greet)], private_data=None)
    outcome = _run(runtime, _start('greet'))
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == 'hi Ada'
    assert prompts == ['name: ']


# --- private workspace assessment --------------------------------------------------


class _Workspace:
    """Toolset owner whose private-workspace check fails on its second assessment."""

    def __init__(self) -> None:
        self.checks = 0

    def sdk_private_workspace(self, _arguments: Mapping[str, object]) -> bool:
        self.checks += 1
        if self.checks > 1:
            message = 'workspace state unreadable'
            raise RuntimeError(message)
        return False

    def edit(self) -> str:
        return 'edited'


def test_workspace_reassessment_failure_refuses_the_call_value_free() -> None:
    """A failing post-call custody check turns success into a private refusal."""
    owner = _Workspace()
    runtime = LocalToolRuntime([_tool('edit', owner.edit)], private_data=None)
    assert _error(_run(runtime, _start('edit'))) == (
        'sdk_private_workspace_error',
        'Private workspace operation refused',
    )
    assert owner.checks == 2  # noqa: PLR2004 - before and after the call.


# --- dependency revisions ----------------------------------------------------------


class _Source(BaseModel):
    value: int


@depends_on_tool('source', 'source', revision_policy='on_validation_error')
class _Consumer(BaseModel):
    source: _Source
    limit: int = Field(gt=0)


class _Unrelated(BaseModel):
    note: str


def _revision_runtime(*extra: ToolMetadata) -> LocalToolRuntime:
    return LocalToolRuntime(
        [
            _tool('source', _Source),
            _tool('consumer', _Consumer),
            _tool('unrelated', _Unrelated),
            *extra,
        ],
        private_data=None,
    )


def _revision_scenario(
    runtime: LocalToolRuntime, *events: StreamEvent
) -> list[ToolOutcomeVariant | None]:
    async def scenario() -> list[ToolOutcomeVariant | None]:
        base = [
            _start('source', {'value': 1}, call_id='source-base'),
            _start('consumer', {'limit': 0}, call_id='consumer-base'),
            _start('unrelated', {'note': 'n'}, call_id='unrelated-base'),
        ]
        return [await runtime.outcome_for_event(event) for event in (*base, *events)]

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    ('event', 'expected'),
    [
        pytest.param(
            _start('source', {'value': 2}, call_id='source-base', lineage=_revision('c', 's')),
            (
                'sdk_dependency_revision_stale',
                'dependency revision call id has already been accepted',
            ),
            id='reused-call-id',
        ),
        pytest.param(
            _start(
                'source',
                {'value': 2},
                call_id='consumer-base',
                lineage=_revision('consumer-base', 'consumer-base'),
            ),
            (
                'sdk_dependency_revision_stale',
                'dependency revision call id has already been accepted',
            ),
            id='root-call-id-reused',
        ),
        pytest.param(
            _start('source', {'value': 2}, call_id='s2', lineage=_revision(None, 'source-base')),
            (
                'sdk_dependency_revision_invalid_lineage',
                'dependency revision requires root and parent call ids',
            ),
            id='missing-root',
        ),
        pytest.param(
            _start(
                'source',
                {'value': 2},
                call_id='s2',
                lineage=_revision('source-base', 'source-base'),
            ),
            (
                'sdk_dependency_revision_root_not_failed',
                'dependency revision root was not a validation failure',
            ),
            id='root-succeeded',
        ),
        pytest.param(
            _start(
                'source',
                {'value': 2},
                call_id='s2',
                lineage=_revision('consumer-base', 'unrelated-base'),
            ),
            (
                'sdk_dependency_revision_parent_mismatch',
                'dependency revision parent does not identify this tool generation',
            ),
            id='parent-other-tool',
        ),
        pytest.param(
            _start(
                'unrelated',
                {'note': 'm'},
                call_id='u2',
                lineage=_revision('consumer-base', 'unrelated-base'),
            ),
            (
                'sdk_dependency_revision_unsupported',
                'dependency revision requires a path to the failed root',
            ),
            id='no-path-to-root',
        ),
        pytest.param(
            _start(
                'consumer',
                {'limit': 5},
                call_id='consumer-retry',
                lineage=_revision('consumer-base', 'consumer-base'),
            ),
            (
                'sdk_dependency_revision_no_dependency_change',
                'dependency revision root has no reconstructed dependency change',
            ),
            id='root-retry-without-change',
        ),
    ],
)
def test_revision_admission_refusals(event: StreamEvent, expected: tuple[str, str]) -> None:
    """Each malformed revision is refused with a fixed code and message."""
    outcomes = _revision_scenario(_revision_runtime(), event)
    assert isinstance(outcomes[0], OkToolOutcome)
    assert _error(outcomes[1])[0] == 'sdk_tool_validation_error'
    assert _error(outcomes[-1]) == expected


def test_revision_of_a_stale_generation_is_refused_after_a_newer_one() -> None:
    """Once a revision supersedes a generation, citing the old parent is stale."""
    outcomes = _revision_scenario(
        _revision_runtime(),
        _start(
            'source', {'value': 2}, call_id='s2', lineage=_revision('consumer-base', 'source-base')
        ),
        _start(
            'source', {'value': 3}, call_id='s3', lineage=_revision('consumer-base', 'source-base')
        ),
        _start('source', {'value': 4}, call_id='s4', lineage=_revision('consumer-base', 's2')),
        _start(
            'consumer',
            {'limit': 5},
            call_id='consumer-fixed',
            lineage=_revision('consumer-base', 'consumer-base'),
        ),
    )
    assert isinstance(outcomes[3], OkToolOutcome)
    assert _error(outcomes[4]) == (
        'sdk_dependency_revision_stale',
        'dependency revision parent is not the current tool generation',
    )
    assert isinstance(outcomes[5], OkToolOutcome)
    fixed = outcomes[6]
    assert isinstance(fixed, OkToolOutcome)
    assert fixed.result == {'source': {'value': 4}, 'limit': 5}


def test_revision_through_a_function_producer_is_unsupported() -> None:
    """Revision frontiers must be SDK Pydantic constructors end to end."""

    def raw_source() -> dict[str, int]:
        return {'value': 1}

    @depends_on_tool('raw_source', 'source', revision_policy='on_validation_error')
    class RawConsumer(BaseModel):
        source: _Source
        limit: int = Field(gt=0)

    runtime = LocalToolRuntime(
        [_tool('raw_source', raw_source), _tool('raw_consumer', RawConsumer)], private_data=None
    )

    async def scenario() -> list[ToolOutcomeVariant | None]:
        return [
            await runtime.outcome_for_event(_start('raw_source', call_id='raw-base')),
            await runtime.outcome_for_event(
                _start('raw_consumer', {'limit': 0}, call_id='raw-consumer')
            ),
            await runtime.outcome_for_event(
                _start('raw_source', call_id='raw-2', lineage=_revision('raw-consumer', 'raw-base'))
            ),
        ]

    outcomes = asyncio.run(scenario())
    assert _error(outcomes[2]) == (
        'sdk_dependency_revision_unsupported',
        'dependency revision requires SDK Pydantic constructors',
    )


# --- serialized lanes and cancellation ---------------------------------------------


@pytest.mark.parametrize('worker_fails', [False, True])
def test_cancelled_serialized_call_holds_its_lane_until_the_worker_exits(
    *, worker_fails: bool
) -> None:
    """Cancellation waits for a running sync writer before the lane is released."""
    started = threading.Event()
    release = threading.Event()
    finished: list[str] = []

    class Editor:
        def write(self) -> str:
            started.set()
            release.wait(5)
            finished.append('write')
            if worker_fails:
                message = 'late write failure'
                raise RuntimeError(message)
            return 'written'

        def read(self) -> str:
            return f'read after {finished}'

    editor = Editor()
    serialized: dict[str, object] = {'serialize_calls': True}
    runtime = LocalToolRuntime(
        [
            _tool('write', editor.write, metadata=serialized),
            _tool('read', editor.read, metadata=serialized),
        ],
        private_data=None,
    )

    async def scenario() -> tuple[bool, ToolOutcomeVariant | None]:
        task = asyncio.create_task(runtime.outcome_for_event(_start('write')))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        still_running = not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return still_running, await runtime.outcome_for_event(_start('read'))

    still_running, read = asyncio.run(scenario())
    assert still_running is True
    assert isinstance(read, OkToolOutcome)
    assert read.result == "read after ['write']"
