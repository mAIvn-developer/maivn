"""Execution-mode regressions for Agent and Swarm developer hooks."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, Swarm
from maivn._internal.hooks import hook_bindings
from maivn._internal.models import StreamEvent
from maivn._internal.scope.execution_hooks import (
    ainvoke_with_scope_hooks,
    scope_execution_hooks,
    stream_with_member_hooks,
    stream_with_scope_hooks,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def _named_hook(name: str) -> Any:
    """Return a stable named hook used to inspect compiled bindings."""

    def callback(_payload: dict[str, object]) -> None:
        return None

    callback.__name__ = name
    return callback


class _StreamClient:
    """Offline client seam that exercises public Agent/Swarm stream wrappers."""

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events

    def stream(self, *_args: object, **_kwargs: object) -> Iterator[StreamEvent]:
        yield from self._events

    async def astream(self, *_args: object, **_kwargs: object) -> AsyncIterator[StreamEvent]:
        for event in self._events:
            yield event


def test_scope_mode_rebuilds_cached_tools_without_scope_callback_per_tool() -> None:
    """Changing mode after compilation cannot retain old per-tool scope hooks."""
    agent = Agent(name='scope-cache', api_key='test-key', base_url='http://testserver')
    agent.before_execute = _named_hook('agent_before')

    @agent.toolify(name='lookup', description='Lookup an item.')
    def lookup(item: str) -> dict[str, str]:
        return {'item': item}

    _ = lookup
    initial_bindings = hook_bindings(
        agent.compile_tools()[0].before_execute,
        target_name='lookup',
    )
    assert [binding.name for binding in initial_bindings] == ['agent_before']

    agent.hook_execution_mode = 'scope'

    bindings = hook_bindings(agent.compile_tools()[0].before_execute, target_name='lookup')
    assert [binding.name for binding in bindings] == []


def test_scope_hooks_wrap_sync_and_async_streams_once_with_safe_events() -> None:
    """Scope mode is an invocation boundary, not a callback per local tool."""
    observed: list[dict[str, object | None]] = []
    agent = Agent(name='scope-run', api_key='test-key', base_url='http://testserver')

    def before(payload: dict[str, object | None]) -> None:
        observed.append(dict(payload))

    def after(payload: dict[str, object | None]) -> None:
        observed.append(dict(payload))

    before.__name__ = 'scope_before'
    after.__name__ = 'scope_after'
    agent.before_execute = before
    agent.after_execute = after
    agent.hook_execution_mode = 'scope'

    source = iter(
        [
            StreamEvent(
                position=3,
                event_type='final',
                data={'session_id': 'ses-real', 'payload': {'response': 'done'}},
            )
        ]
    )
    events = list(
        stream_with_scope_hooks(
            source,
            scope_execution_hooks(agent),
            scope=agent,
            messages=[{'role': 'user', 'content': 'SECRET-MUST-STAY-LOCAL'}],
        )
    )

    assert [event.event_type for event in events] == ['hook_fired', 'hook_fired', 'final']
    assert [event.payload['name'] for event in events if event.event_type == 'hook_fired'] == [
        'scope_before',
        'scope_after',
    ]
    hook_events = [event for event in events if event.event_type == 'hook_fired']
    assert all(event.data['session_id'] == 'ses-real' for event in hook_events)
    assert len({event.data['event_id'] for event in hook_events}) == len(hook_events)
    assert all('SECRET-MUST-STAY-LOCAL' not in str(event.data) for event in events)
    assert [(payload['stage'], payload['tool']) for payload in observed] == [
        ('before', None),
        ('after', None),
    ]
    assert observed[-1]['result'] == {'response': 'done'}

    async def async_source() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(position=4, event_type='final', data={'payload': {'response': 'again'}})

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in ainvoke_with_scope_hooks(
                async_source,
                scope_execution_hooks(agent),
                scope=agent,
                messages=[],
            )
        ]

    async_events = asyncio.run(collect())
    assert [event.event_type for event in async_events] == ['hook_fired', 'hook_fired', 'final']
    assert [(payload['stage'], payload['tool']) for payload in observed] == [
        ('before', None),
        ('after', None),
        ('before', None),
        ('after', None),
    ]


def test_scope_after_runs_for_stream_error_and_parent_chain_is_not_duplicated() -> None:
    """Errors still close scope hooks in reverse order once per invocation."""
    order: list[str] = []
    agent = Agent(name='member', api_key='test-key', base_url='http://testserver')
    swarm = Swarm(name='outer', agents=[agent])
    swarm.hook_execution_mode = 'scope'
    agent.hook_execution_mode = 'scope'
    swarm.before_execute = _named_hook('swarm_before')
    swarm.after_execute = _named_hook('swarm_after')
    agent.before_execute = _named_hook('agent_before')
    agent.after_execute = _named_hook('agent_after')

    def record(name: str) -> Any:
        def callback(payload: dict[str, object | None]) -> None:
            order.append(f'{name}:{payload["tool"]}')

        return callback

    for scope, attribute, name in (
        (swarm, 'before_execute', 'swarm_before'),
        (agent, 'before_execute', 'agent_before'),
        (agent, 'after_execute', 'agent_after'),
        (swarm, 'after_execute', 'swarm_after'),
    ):
        setattr(scope, attribute, record(name))

    def broken() -> Any:
        yield StreamEvent(position=7, event_type='status_message', data={'payload': {}})
        message = 'intentional failure'
        raise RuntimeError(message)

    iterator = stream_with_scope_hooks(
        broken(),
        scope_execution_hooks(agent),
        scope=agent,
        messages=[],
    )
    assert next(iterator).event_type == 'status_message'
    assert next(iterator).event_type == 'hook_fired'
    assert next(iterator).event_type == 'hook_fired'
    assert next(iterator).event_type == 'hook_fired'
    assert next(iterator).event_type == 'hook_fired'
    with pytest.raises(RuntimeError, match='intentional failure'):
        next(iterator)
    assert order == [
        'swarm_before:None',
        'agent_before:None',
        'agent_after:None',
        'swarm_after:None',
    ]


def test_swarm_agent_mode_wraps_exact_assignment_once_not_each_member_tool() -> None:
    """The assignment id closes the same member boundary that it opened."""
    calls: list[tuple[str, object | None]] = []
    member = Agent(name='member', api_key='test-key', base_url='http://testserver')
    swarm = Swarm(name='swarm', agents=[member], hook_execution_mode='agent')

    def before(payload: dict[str, object | None]) -> None:
        calls.append(('before', payload['tool']))

    def after(payload: dict[str, object | None]) -> None:
        calls.append(('after', payload['tool']))

    swarm.before_execute = before
    swarm.after_execute = after
    events = list(
        stream_with_member_hooks(
            iter(
                [
                    StreamEvent(
                        position=1,
                        event_type='agent_assignment',
                        data={
                            'payload': {
                                'assignment_id': 'assignment-1',
                                'agent_name': 'member',
                                'status': 'executing',
                            }
                        },
                    ),
                    StreamEvent(
                        position=2,
                        event_type='system_tool_start',
                        data={'payload': {'tool_name': 'first'}},
                    ),
                    StreamEvent(
                        position=3,
                        event_type='system_tool_start',
                        data={'payload': {'tool_name': 'second'}},
                    ),
                    StreamEvent(
                        position=4,
                        event_type='agent_assignment',
                        data={
                            'payload': {
                                'assignment_id': 'assignment-1',
                                'agent_name': 'member',
                                'status': 'completed',
                                'result': {'answer': 'done'},
                            }
                        },
                    ),
                ]
            ),
            swarm,
        )
    )

    assert [event.event_type for event in events] == [
        'hook_fired',
        'agent_assignment',
        'system_tool_start',
        'system_tool_start',
        'hook_fired',
        'agent_assignment',
    ]
    assert calls == [('before', member), ('after', member)]


def test_scope_stream_cancellation_still_runs_after_without_ignoring_close() -> None:
    """Closing a partially consumed stream must release its scope lifecycle."""
    calls: list[str] = []
    source_closed: list[bool] = []
    agent = Agent(name='cancel', api_key='test-key', base_url='http://testserver')
    agent.hook_execution_mode = 'scope'

    def before(_payload: dict[str, object | None]) -> None:
        calls.append('before')

    def after(_payload: dict[str, object | None]) -> None:
        calls.append('after')

    agent.before_execute = before
    agent.after_execute = after

    def endless() -> Any:
        try:
            while True:
                yield StreamEvent(position=1, event_type='status_message', data={'payload': {}})
        finally:
            source_closed.append(True)

    iterator = stream_with_scope_hooks(
        endless(),
        scope_execution_hooks(agent),
        scope=agent,
        messages=[],
    )
    assert next(iterator).event_type == 'status_message'
    iterator.close()
    assert calls == ['before', 'after']
    assert source_closed == [True]

    async_calls: list[str] = []
    async_closed: list[bool] = []

    def async_before(_payload: dict[str, object | None]) -> None:
        async_calls.append('before')

    def async_after(_payload: dict[str, object | None]) -> None:
        async_calls.append('after')

    agent.before_execute = async_before
    agent.after_execute = async_after

    async def async_endless() -> AsyncIterator[StreamEvent]:
        try:
            while True:
                yield StreamEvent(position=1, event_type='status_message', data={'payload': {}})
        finally:
            async_closed.append(True)

    async def cancel_async_stream() -> None:
        async_iterator = ainvoke_with_scope_hooks(
            async_endless,
            scope_execution_hooks(agent),
            scope=agent,
            messages=[],
        )
        assert (await async_iterator.__anext__()).event_type == 'status_message'
        await async_iterator.aclose()

    asyncio.run(cancel_async_stream())
    assert async_calls == ['before', 'after']
    assert async_closed == [True]


def test_scope_hook_failure_is_safe_and_has_invocation_unique_event_identity() -> None:
    """Local observed events identify callbacks without exposing callback exceptions."""
    agent = Agent(name='safe-hook', api_key='test-key', base_url='http://testserver')
    agent.hook_execution_mode = 'scope'

    def fail(_payload: dict[str, object | None]) -> None:
        marker = 'callback-private-marker'
        raise RuntimeError(marker)

    agent.before_execute = fail
    event_sets = [
        list(
            stream_with_scope_hooks(
                iter([StreamEvent(position=1, event_type='final', data={'payload': {}})]),
                scope_execution_hooks(agent),
                scope=agent,
                messages=[],
            )
        )
        for _ in range(2)
    ]
    hook_events = [
        event for events in event_sets for event in events if event.event_type == 'hook_fired'
    ]
    assert [event.payload['error'] for event in hook_events] == ['RuntimeError', 'RuntimeError']
    assert all('callback-private-marker' not in str(event.data) for event in hook_events)
    assert len({event.data['event_id'] for event in hook_events}) == len(hook_events)


def test_mixed_member_modes_use_assignment_ids_in_sync_and_async_streams() -> None:
    """Each member boundary uses its assignment identity across both stream APIs."""
    calls: list[tuple[str, str, object | None]] = []

    def hook(name: str) -> Any:
        def callback(payload: dict[str, object | None]) -> None:
            calls.append((name, cast('str', payload['stage']), payload['tool']))

        callback.__name__ = name
        return callback

    member_scope = Agent(
        name='scope-member',
        api_key='test-key',
        base_url='http://testserver',
        hook_execution_mode='scope',
    )
    member_tool = Agent(name='tool-member', api_key='test-key', base_url='http://testserver')
    swarm = Swarm(
        name='mixed-swarm',
        agents=[member_scope, member_tool],
        hook_execution_mode='agent',
    )
    member_scope.before_execute = hook('member_before')
    member_scope.after_execute = hook('member_after')
    swarm.before_execute = hook('swarm_before')
    swarm.after_execute = hook('swarm_after')
    source_events = [
        StreamEvent(
            position=1,
            event_type='sdk_session',
            data={'session_id': 'ses-hooks', 'payload': {}},
        ),
        StreamEvent(
            position=2,
            event_type='agent_assignment',
            data={
                'session_id': 'ses-hooks',
                'payload': {
                    'assignment_id': 'assignment-scope',
                    'agent_name': 'scope-member',
                    'status': 'executing',
                },
            },
        ),
        StreamEvent(
            position=3,
            event_type='agent_assignment',
            data={
                'session_id': 'ses-hooks',
                'payload': {
                    'assignment_id': 'assignment-tool',
                    'agent_name': 'tool-member',
                    'status': 'executing',
                },
            },
        ),
        StreamEvent(
            position=4,
            event_type='agent_assignment',
            data={
                'session_id': 'ses-hooks',
                'payload': {
                    'assignment_id': 'assignment-scope',
                    'agent_name': 'scope-member',
                    'status': 'completed',
                    'result': {'answer': 'scope'},
                },
            },
        ),
        StreamEvent(
            position=5,
            event_type='agent_assignment',
            data={
                'session_id': 'ses-hooks',
                'payload': {
                    'assignment_id': 'assignment-tool',
                    'agent_name': 'tool-member',
                    'status': 'failed',
                    'error': 'provider failed',
                },
            },
        ),
        StreamEvent(
            position=6,
            event_type='final',
            data={'session_id': 'ses-hooks', 'payload': {}},
        ),
    ]
    client = _StreamClient(source_events)
    member_scope.client = cast('Any', client)
    member_tool.client = cast('Any', client)
    swarm.client = cast('Any', client)

    def hook_targets(events: list[StreamEvent]) -> list[tuple[str, str, str]]:
        return [
            (
                cast('str', event.payload['name']),
                cast('str', event.payload['stage']),
                cast('str', event.payload['target_id']),
            )
            for event in events
            if event.event_type == 'hook_fired'
        ]

    sync_events = list(swarm.stream('hello'))
    assert hook_targets(sync_events) == [
        ('swarm_before', 'before', 'assignment-scope'),
        ('member_before', 'before', 'assignment-scope'),
        ('swarm_before', 'before', 'assignment-tool'),
        ('member_after', 'after', 'assignment-scope'),
        ('swarm_after', 'after', 'assignment-scope'),
        ('swarm_after', 'after', 'assignment-tool'),
    ]
    assert all(
        event.data['session_id'] == 'ses-hooks'
        for event in sync_events
        if event.event_type == 'hook_fired'
    )

    async def collect() -> list[StreamEvent]:
        return [event async for event in swarm.astream('hello')]

    async_events = asyncio.run(collect())
    assert hook_targets(async_events) == hook_targets(sync_events)
    all_hook_events = [
        event for event in [*sync_events, *async_events] if event.event_type == 'hook_fired'
    ]
    assert len({event.data['event_id'] for event in all_hook_events}) == len(all_hook_events)
    assert calls.count(('member_before', 'before', member_scope)) == calls.count(
        ('member_after', 'after', member_scope)
    )


def test_public_client_observes_raw_swarm_progress_assignments() -> None:
    """Raw data-plane swarm progress, not reporter projection, drives member hooks."""
    callback_errors: list[object | None] = []
    raw_events: list[dict[str, object]] = [
        {
            'event_id': 'evt-member-start',
            'type': 'progress_update',
            'session_id': 'ses-raw-swarm',
            'root_event_id': 'evt-root',
            'payload': {
                'stage': 'swarm_agent',
                'action_type': 'swarm_agent',
                'action_id': 'assignment-raw-member',
                'action_name': 'raw-member',
                'status': 'executing',
            },
        },
        {
            'event_id': 'evt-member-failed',
            'type': 'progress_update',
            'session_id': 'ses-raw-swarm',
            'root_event_id': 'evt-root',
            'payload': {
                'stage': 'swarm_agent',
                'action_type': 'swarm_agent',
                'action_id': 'assignment-raw-member',
                'action_name': 'raw-member',
                'status': 'failed',
            },
        },
        {
            'event_id': 'evt-final',
            'type': 'final',
            'session_id': 'ses-raw-swarm',
            'root_event_id': 'evt-root',
            'payload': {
                'message': {
                    'message_id': 'msg-final',
                    'role': 'assistant',
                    'content': 'done',
                    'ts': '2026-09-07T00:00:00Z',
                },
                'usage': {},
                'tool_calls': {'count': 0, 'names': []},
            },
        },
    ]
    sse = ''.join(
        f'id: {position}\nevent: {event["type"]}\ndata: {json.dumps(event)}\n\n'
        for position, event in enumerate(raw_events, start=1)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-raw-swarm', 'stream_position': 0},
            )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=sse,
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    member = Agent(name='raw-member', client=client)
    swarm = Swarm(name='raw-swarm', agents=[member], hook_execution_mode='agent')

    def after(payload: dict[str, object | None]) -> None:
        callback_errors.append(payload['error'])

    swarm.before_execute = _named_hook('raw_before')
    swarm.after_execute = after
    observed = list(swarm.stream('run member'))
    hook_events = [event for event in observed if event.event_type == 'hook_fired']

    assert [(event.payload['stage'], event.payload['target_id']) for event in hook_events] == [
        ('before', 'assignment-raw-member'),
        ('after', 'assignment-raw-member'),
    ]
    assert all(event.data['session_id'] == 'ses-raw-swarm' for event in hook_events)
    assert callback_errors == ['swarm_member_failed']


def test_public_agent_and_swarm_streams_apply_scope_and_assignment_modes() -> None:
    """The public stream surfaces use the shared lifecycle wrappers once each."""
    agent_calls: list[str] = []
    agent = Agent(
        name='public-agent',
        client=cast('Any', _StreamClient([StreamEvent(position=1, event_type='final', data={})])),
        hook_execution_mode='scope',
    )

    def agent_before(_payload: dict[str, object | None]) -> None:
        agent_calls.append('before')

    def agent_after(_payload: dict[str, object | None]) -> None:
        agent_calls.append('after')

    agent.before_execute = agent_before
    agent.after_execute = agent_after
    assert [event.event_type for event in agent.stream('hello')] == [
        'hook_fired',
        'hook_fired',
        'final',
    ]
    assert agent_calls == ['before', 'after']

    member_calls: list[str] = []
    member = Agent(
        name='public-member',
        client=cast(
            'Any',
            _StreamClient(
                [
                    StreamEvent(
                        position=2,
                        event_type='agent_assignment',
                        data={
                            'payload': {
                                'assignment_id': 'assignment-public',
                                'agent_name': 'public-member',
                                'status': 'executing',
                            }
                        },
                    ),
                    StreamEvent(
                        position=3,
                        event_type='system_tool_start',
                        data={'payload': {}},
                    ),
                    StreamEvent(
                        position=4,
                        event_type='agent_assignment',
                        data={
                            'payload': {
                                'assignment_id': 'assignment-public',
                                'agent_name': 'public-member',
                                'status': 'completed',
                            }
                        },
                    ),
                ]
            ),
        ),
    )
    swarm = Swarm(name='public-swarm', agents=[member], hook_execution_mode='agent')

    def member_before(_payload: dict[str, object | None]) -> None:
        member_calls.append('before')

    def member_after(_payload: dict[str, object | None]) -> None:
        member_calls.append('after')

    swarm.before_execute = member_before
    swarm.after_execute = member_after
    assert [event.event_type for event in swarm.stream('hello')] == [
        'hook_fired',
        'agent_assignment',
        'system_tool_start',
        'hook_fired',
        'agent_assignment',
    ]
    assert member_calls == ['before', 'after']
