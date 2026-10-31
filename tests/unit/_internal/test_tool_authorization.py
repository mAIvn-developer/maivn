"""Privileged labels are enforced from the original callable on every attempt."""

# pyright: reportPrivateUsage=false
# Reuse the invocation suite's event and wire builders.
import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, cast

import pytest
from maivn_contracts.tools import ToolCall

from maivn._internal.client_stream import resolve_canonical_tool_interrupts
from maivn._internal.compat.decorators import depends_on_interrupt
from maivn._internal.models import RunOptions, StreamEvent, ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn._internal.wire import _function_tool_spec, invocation_tools
from tests.unit._internal.test_runtime_tool_runtime_characterization import _start

if TYPE_CHECKING:
    from maivn._internal.client import Client as SdkClient


@pytest.mark.parametrize(
    'metadata',
    [
        {'destructive': True},
        {'permissions': ['delete']},
        {'permissions': ['admin']},
        {'permissions': ['impersonate']},
        {'permissions': ['import']},
    ],
)
def test_privileged_calls_never_reach_target_or_hooks(metadata: dict[str, object]) -> None:
    """Verify privileged calls never reach target or hooks."""
    calls: list[str] = []

    def before(_payload: object) -> None:
        calls.append('hook')

    tool = ToolMetadata(
        name='danger',
        target=lambda: calls.append('target'),
        metadata=metadata,
        before_execute=before,
    )
    runtime = LocalToolRuntime([tool], private_data=None)
    for attempt in range(2):
        outcome = asyncio.run(
            runtime.outcome_for_event(_start('danger', call_id=f'call-{attempt}'))
        )
        assert outcome is not None
        assert outcome.status == 'error'
        assert outcome.error.code == 'tool_authorization_required'
    assert calls == []


@pytest.mark.parametrize(
    'metadata',
    [{}, {'permissions': ['read']}, {'permissions': ['write']}, {'permissions': ['export']}],
)
def test_explicitly_registered_ordinary_tools_remain_authorized(
    metadata: dict[str, object],
) -> None:
    """Verify explicitly registered ordinary tools remain authorized."""
    calls: list[str] = []
    tool = ToolMetadata(name='ordinary', target=lambda: calls.append('target'), metadata=metadata)
    outcome = asyncio.run(
        LocalToolRuntime([tool], private_data=None).outcome_for_event(_start('ordinary'))
    )
    assert outcome is not None
    assert outcome.status == 'ok'
    assert calls == ['target']


def test_original_labels_cross_the_wire() -> None:
    """Verify original labels cross the wire."""
    tool = ToolMetadata(
        name='danger',
        target=lambda: None,
        metadata={'permissions': ['delete'], 'destructive': True},
    )
    payload = _function_tool_spec(tool)
    assert payload['permissions'] == ['delete']
    assert payload['destructive'] is True


def test_explicit_opt_out_is_preserved_and_allows_local_execution() -> None:
    """The opt-out is a code declaration visible to the server, never a model argument."""
    calls: list[str] = []
    tool = ToolMetadata(
        name='danger',
        target=lambda: calls.append('target'),
        metadata={'destructive': True, 'approval_policy': 'allow'},
    )
    assert _function_tool_spec(tool)['approval_policy'] == 'allow'
    outcome = asyncio.run(
        LocalToolRuntime([tool], private_data=None).outcome_for_event(_start('danger'))
    )
    assert outcome is not None
    assert outcome.status == 'ok'
    assert calls == ['target']


def test_local_approval_is_one_call_and_never_inherited_by_descendants() -> None:
    """A server start event alone cannot defeat the original local privileged label."""
    calls: list[str] = []
    tool = ToolMetadata(
        name='danger',
        target=lambda: calls.append('target'),
        metadata={'destructive': True, 'approval_policy': 'ask'},
    )
    runtime = LocalToolRuntime([tool], private_data=None)
    event = _start('danger')
    call = ToolCall.model_validate(event.payload['tool_call'])
    runtime.record_tool_approval(call)
    child = call.model_copy(
        update={'lineage': call.lineage.model_copy(update={'session_id': 'child'})}
    )
    child_event = event.model_copy(update={'data': {'tool_call': child.model_dump(mode='json')}})
    child_outcome = asyncio.run(runtime.outcome_for_event(child_event))
    assert child_outcome is not None
    assert child_outcome.status == 'error'
    outcome = asyncio.run(runtime.outcome_for_event(event))
    assert outcome is not None
    assert outcome.status == 'ok'
    replay = asyncio.run(runtime.outcome_for_event(event))
    assert replay is not None
    assert replay.status == 'error'
    assert calls == ['target']


@pytest.mark.parametrize('approved', [True, False])
def test_durable_interrupt_uses_registered_handler_and_records_local_consent(
    *,
    approved: bool,
) -> None:
    """A successful response permits execution only after the local handler agreed."""
    calls: list[str] = []
    prompts: list[str] = []

    def handler(prompt: str) -> bool:
        prompts.append(prompt)
        assert 'PRIVATE-RECORD' not in prompt
        return approved

    def target(secret: str) -> None:
        calls.append(secret)

    tool = ToolMetadata(
        name='danger',
        target=target,
        metadata={
            'destructive': True,
            'approval_policy': 'ask',
            'approval_handler': handler,
        },
    )
    runtime = LocalToolRuntime([tool], private_data=None)
    start = _start('danger', {'secret': 'PRIVATE-RECORD'})
    call = ToolCall.model_validate(start.payload['tool_call'])
    submitted: list[object] = []

    class ApprovalClient:
        async def asubmit_interrupt_response(self, *_args: object, **kwargs: object) -> None:
            """Capture the network response while retaining the real interrupt resolver."""
            assert calls == []
            submitted.append(kwargs['answer'])

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            position=0,
            event_type='interrupt_required',
            data={
                'interrupt': {
                    'kind': 'tool_argument',
                    'tool_name': 'danger',
                    'argument_name': '__tool_approval__',
                    'thread_id': 'thread',
                    'checkpoint_id': f'tool-approval-{call.lineage.session_id}-{call.call_id}',
                    'session_id': call.lineage.session_id,
                    'question': 'PRIVATE-RECORD',
                    'tool_call': call.model_copy(update={'arguments': {}}).model_dump(mode='json'),
                }
            },
        )
        yield start

    async def run() -> None:
        async for event in resolve_canonical_tool_interrupts(
            events(),
            client=cast('SdkClient', ApprovalClient()),
            tools=[tool],
            options=RunOptions(),
            tool_runtime=runtime,
        ):
            if event.event_type == 'system_tool_start':
                result = await runtime.outcome_for_event(event)
                assert result is not None
                assert result.status == ('ok' if approved else 'error')

    asyncio.run(run())
    assert len(prompts) == 1
    assert submitted == [approved]
    assert calls == (['PRIVATE-RECORD'] if approved else [])


def test_legacy_argument_named_like_approval_keeps_its_own_handler() -> None:
    """The new control marker cannot consume an existing caller-defined argument."""
    submitted: list[object] = []

    @depends_on_interrupt('__tool_approval__', lambda _prompt: 'legacy-value')
    def target() -> None:
        pass

    class ApprovalClient:
        async def asubmit_interrupt_response(self, *_args: object, **kwargs: object) -> None:
            """Record the legacy answer sent through the existing response endpoint."""
            submitted.append(kwargs['answer'])

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            position=0,
            event_type='interrupt_required',
            data={
                'interrupt': {
                    'kind': 'tool_argument',
                    'tool_name': 'legacy',
                    'argument_name': '__tool_approval__',
                    'thread_id': 'thread',
                    'checkpoint_id': 'tool-argument-session-call-__tool_approval__',
                    'question': 'Legacy question?',
                }
            },
        )

    async def run() -> None:
        async for _ in resolve_canonical_tool_interrupts(
            events(),
            client=cast('SdkClient', ApprovalClient()),
            tools=[ToolMetadata(name='legacy', target=target)],
            options=RunOptions(),
        ):
            pass

    asyncio.run(run())
    assert submitted == ['legacy-value']


def test_approval_for_one_registration_cannot_authorize_another() -> None:
    """A mismatched interrupt must not ask A's handler to authorize B's callable."""
    calls: list[str] = []

    def handler(_prompt: str) -> bool:
        calls.append('handler-A')
        return True

    tools = [
        ToolMetadata(
            name='tool_A',
            target=lambda: None,
            metadata={'destructive': True, 'approval_policy': 'ask', 'approval_handler': handler},
        ),
        ToolMetadata(
            name='tool_B',
            target=lambda: calls.append('target-B'),
            metadata={'destructive': True, 'approval_policy': 'ask'},
        ),
    ]
    runtime = LocalToolRuntime(tools, private_data=None)
    start = _start('tool_B')
    call = ToolCall.model_validate(start.payload['tool_call'])

    class ApprovalClient:
        async def asubmit_interrupt_response(self, *_args: object, **_kwargs: object) -> None:
            """Detect an invalid approval reaching the response endpoint."""
            calls.append('submitted')

    async def events() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(
            position=0,
            event_type='interrupt_required',
            data={
                'interrupt': {
                    'kind': 'tool_argument',
                    'tool_name': 'tool_A',
                    'argument_name': '__tool_approval__',
                    'thread_id': 'thread',
                    'checkpoint_id': f'tool-approval-{call.lineage.session_id}-{call.call_id}',
                    'session_id': call.lineage.session_id,
                    'tool_call': call.model_dump(mode='json'),
                }
            },
        )
        yield start

    async def run() -> None:
        async for event in resolve_canonical_tool_interrupts(
            events(),
            client=cast('SdkClient', ApprovalClient()),
            tools=tools,
            options=RunOptions(),
            tool_runtime=runtime,
        ):
            if event.event_type == 'system_tool_start':
                result = await runtime.outcome_for_event(event)
                assert result is not None
                assert result.status == 'error'

    asyncio.run(run())
    assert calls == []


def test_nested_owners_cannot_silently_share_conflicting_approval_policy() -> None:
    """One broker name must never silently choose a parent's policy for its child."""

    def target() -> None:
        pass

    parent = ToolMetadata(
        name='shared', target=target, metadata={'destructive': True, 'approval_policy': 'allow'}
    )
    child = ToolMetadata(name='shared', target=target, metadata={'destructive': True})

    class Owner:
        def compile_tools(self) -> list[ToolMetadata]:
            """Return the descendant owner's registered tool."""
            return [child]

    with pytest.raises(ValueError, match='Conflicting local tool approval policy'):
        invocation_tools([parent], [Owner()])
    # The existing no-choice registration behavior remains unchanged.
    assert invocation_tools([child], [Owner()]) == [child]


def test_descendant_keeps_its_own_ask_policy_when_parent_allows() -> None:
    """A parent's explicit opt-out cannot grant its descendant execution permission."""
    calls: list[str] = []
    parent = ToolMetadata(
        name='parent_tool',
        target=lambda: calls.append('parent'),
        metadata={'destructive': True, 'approval_policy': 'allow'},
    )
    child = ToolMetadata(
        name='child_tool',
        target=lambda: calls.append('child'),
        metadata={'destructive': True, 'approval_policy': 'ask'},
    )

    class Owner:
        def compile_tools(self) -> list[ToolMetadata]:
            """Return the descendant's distinct registered name and policy."""
            return [child]

    tools = invocation_tools([parent], [Owner()])
    assert [_function_tool_spec(tool)['approval_policy'] for tool in tools] == ['allow', 'ask']
    runtime = LocalToolRuntime(tools, private_data=None)
    parent_result = asyncio.run(runtime.outcome_for_event(_start('parent_tool')))
    child_result = asyncio.run(
        runtime.outcome_for_event(
            _start('child_tool', lineage={'session_id': 'child', 'invocation_id': 'child-inv'})
        )
    )
    assert parent_result is not None
    assert parent_result.status == 'ok'
    assert child_result is not None
    assert child_result.status == 'error'
    assert calls == ['parent']


@pytest.mark.parametrize(
    'metadata', [{'permissions': ['billing']}, {'destructive': 'false'}, {'permissions': 'read'}]
)
def test_historical_freeform_labels_become_denied_tools_without_crashing_run(
    metadata: dict[str, object],
) -> None:
    """Unknown legacy labels deny the tool consistently at wire and local execution boundaries."""
    calls: list[str] = []
    tool = ToolMetadata(name='legacy', target=lambda: calls.append('target'), metadata=metadata)
    payload = _function_tool_spec(tool)
    assert payload['destructive'] is True
    result = asyncio.run(
        LocalToolRuntime([tool], private_data=None).outcome_for_event(_start('legacy'))
    )
    assert result is not None
    assert result.status == 'error'
    assert result.error.code == 'tool_authorization_required'
    assert calls == []
