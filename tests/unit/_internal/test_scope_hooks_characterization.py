"""Characterization tests for invocation and member-assignment execution hooks."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, MaivnHTTPError, StreamEvent, Swarm

JsonObject = dict[str, Any]
_FINAL_PAYLOAD: JsonObject = {
    'message': {
        'message_id': 'msg-final',
        'role': 'assistant',
        'content': 'done',
        'ts': '2026-07-04T12:00:00Z',
    },
    'usage': {},
    'tool_calls': {'count': 0, 'names': []},
}


def _sse(events: list[tuple[str, JsonObject]]) -> str:
    frames: list[str] = []
    for position, (event_type, payload) in enumerate(events, start=1):
        event = {
            'event_id': f'evt-{position}',
            'ordinal': f'ord-{position}',
            'type': event_type,
            'session_id': 'ses-hooks',
            'root_event_id': 'evt-root',
            'payload': payload,
            'ts': '2026-07-04T12:00:00Z',
        }
        frames.append(f'id: {position}\nevent: {event_type}\ndata: {json.dumps(event)}\n\n')
    return ''.join(frames)


def _client(
    *,
    invoke_status: int = HTTPStatus.ACCEPTED,
    events: str = '',
    events_status: int = HTTPStatus.OK,
) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            if invoke_status != HTTPStatus.ACCEPTED:
                return httpx.Response(invoke_status, json={'detail': 'invoke refused'})
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-hooks', 'stream_position': 0}
            )
        if events_status != HTTPStatus.OK:
            return httpx.Response(events_status, json={'detail': 'stream broke'})
        return httpx.Response(
            HTTPStatus.OK, headers={'content-type': 'text/event-stream'}, content=events
        )

    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )


class _Recorder:
    """Named hook callbacks that record the payloads they receive."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None, str | None]] = []

    def hook(self, name: str) -> Any:
        def callback(payload: dict[str, object | None]) -> None:
            error = payload['error']
            self.seen.append(
                (name, cast('str | None', payload['stage']), None if error is None else str(error))
            )

        callback.__name__ = name
        return callback


def _hook_summary(events: list[StreamEvent]) -> list[tuple[str, object, object, object]]:
    return [
        (
            event.event_type,
            event.payload.get('name'),
            event.payload.get('stage'),
            event.payload.get('target_id'),
        )
        if event.event_type == 'hook_fired'
        else (event.event_type, None, None, None)
        for event in events
    ]


def _scoped_agent(client: Client, recorder: _Recorder) -> Agent:
    agent = Agent(name='scoped', client=client, hook_execution_mode='scope')
    agent.before_execute = recorder.hook('before')
    agent.after_execute = recorder.hook('after')
    return agent


def test_failed_blocking_invoke_still_runs_after_hooks_with_the_error() -> None:
    """A refused invoke reaches the after hook with the raised error, then propagates."""
    recorder = _Recorder()
    agent = _scoped_agent(_client(invoke_status=HTTPStatus.BAD_REQUEST), recorder)

    with pytest.raises(MaivnHTTPError) as raised:
        _ = agent.invoke('go')

    assert recorder.seen == [('before', 'before', None), ('after', 'after', str(raised.value))]


def test_failed_async_stream_emits_the_after_hook_event_before_raising() -> None:
    """An async stream failure yields the after-hook trace event, then re-raises."""
    recorder = _Recorder()
    agent = _scoped_agent(_client(events_status=HTTPStatus.INTERNAL_SERVER_ERROR), recorder)
    observed: list[StreamEvent] = []

    async def consume() -> None:
        async for event in agent.astream('go'):
            observed.append(event)  # noqa: PERF401 - keep events seen before the error.

    with pytest.raises(MaivnHTTPError):
        asyncio.run(consume())

    assert _hook_summary(observed) == [('hook_fired', 'after', 'after', 'scoped')]
    assert observed[0].position == 0
    assert [entry[:2] for entry in recorder.seen] == [('before', 'before'), ('after', 'after')]


def test_async_stream_without_final_event_still_closes_the_scope() -> None:
    """A stream that ends without a final event fires after hooks without a trace event."""
    recorder = _Recorder()
    events = _sse([('status_message', {'message': 'working'})])
    agent = _scoped_agent(_client(events=events), recorder)

    async def consume() -> list[StreamEvent]:
        return [event async for event in agent.astream('go')]

    observed = asyncio.run(consume())

    assert _hook_summary(observed) == [
        ('status_message', None, None, None),
        ('hook_fired', 'before', 'before', 'scoped'),
    ]
    assert recorder.seen == [('before', 'before', None), ('after', 'after', None)]


def _assignment(
    action_id: str, name: str, status: object, action_type: str = 'swarm_agent'
) -> tuple[str, JsonObject]:
    return (
        'progress_update',
        {
            'stage': action_type,
            'action_type': action_type,
            'action_id': action_id,
            'action_name': name,
            'status': status,
        },
    )


_MEMBER_EVENTS = _sse(
    [
        _assignment('tool-1', 'member', 'running', action_type='tool'),
        _assignment('a1', 'member', ' RUNNING '),
        _assignment('a3', '', 'running'),
        _assignment('a4', 'member', 7),
        _assignment('a2', 'member', 'done'),
        _assignment('a1', 'member', 'paused'),
        _assignment('a1', 'member', 'cancelled'),
        ('final', _FINAL_PAYLOAD),
    ],
)
_MEMBER_TRACE = [
    ('progress_update', None, None, None),
    ('hook_fired', 'swarm_before', 'before', 'a1'),
    ('hook_fired', 'member_before', 'before', 'a1'),
    ('progress_update', None, None, None),
    ('progress_update', None, None, None),
    ('progress_update', None, None, None),
    ('progress_update', None, None, None),
    ('progress_update', None, None, None),
    ('progress_update', None, None, None),
    ('final', None, None, None),
]


def _member_swarm(recorder: _Recorder) -> Swarm:
    client = _client(events=_MEMBER_EVENTS)
    member = Agent(name='member', client=client, hook_execution_mode='scope')
    member.before_execute = recorder.hook('member_before')
    swarm = Swarm(name='team', agents=[member], hook_execution_mode='agent')
    swarm.before_execute = recorder.hook('swarm_before')
    return swarm


def test_member_hooks_follow_only_well_formed_assignments() -> None:
    """Only a well-formed known assignment starts member hooks; malformed ones pass through."""
    recorder = _Recorder()

    observed = list(_member_swarm(recorder).stream('go'))

    assert _hook_summary(observed) == _MEMBER_TRACE
    assert recorder.seen == [('swarm_before', 'before', None), ('member_before', 'before', None)]


def test_async_member_hooks_match_the_sync_trace() -> None:
    """The async swarm stream produces the same member lifecycle trace."""
    recorder = _Recorder()
    swarm = _member_swarm(recorder)

    async def consume() -> list[StreamEvent]:
        return [event async for event in swarm.astream('go')]

    assert _hook_summary(asyncio.run(consume())) == _MEMBER_TRACE


def test_swarm_invoke_with_agent_mode_hooks_observes_assignments() -> None:
    """A blocking swarm invoke switches to the stream path when member hooks exist."""
    recorder = _Recorder()
    swarm = _member_swarm(recorder)

    response = swarm.invoke('go')
    async_response = asyncio.run(swarm.ainvoke('again'))

    assert response.response == async_response.response == 'done'
    assert recorder.seen == [
        ('swarm_before', 'before', None),
        ('member_before', 'before', None),
        ('swarm_before', 'before', None),
        ('member_before', 'before', None),
    ]


def test_completed_assignment_runs_member_after_before_swarm_after() -> None:
    """Completion, failure, and cancellation fire member then swarm after hooks with the error."""
    recorder = _Recorder()
    events = _sse(
        [
            _assignment('a1', 'member', 'in_progress'),
            ('progress_update', {**_assignment('a1', 'member', 'success')[1], 'result': 'ok'}),
            _assignment('a2', 'member', 'executing'),
            _assignment('a2', 'member', 'error'),
            _assignment('a3', 'member', 'executing'),
            (
                'progress_update',
                {**_assignment('a3', 'member', 'cancelled')[1], 'error': 'operator stop'},
            ),
            ('final', _FINAL_PAYLOAD),
        ],
    )
    member = Agent(name='member', client=_client(events=events), hook_execution_mode='scope')
    member.after_execute = recorder.hook('member_after')
    swarm = Swarm(name='team', agents=[member], hook_execution_mode='agent')
    swarm.after_execute = recorder.hook('swarm_after')

    observed = list(swarm.stream('go'))

    assert [entry for entry in _hook_summary(observed) if entry[0] == 'hook_fired'] == [
        ('hook_fired', 'member_after', 'after', 'a1'),
        ('hook_fired', 'swarm_after', 'after', 'a1'),
        ('hook_fired', 'member_after', 'after', 'a2'),
        ('hook_fired', 'swarm_after', 'after', 'a2'),
        ('hook_fired', 'member_after', 'after', 'a3'),
        ('hook_fired', 'swarm_after', 'after', 'a3'),
    ]
    assert recorder.seen == [
        ('member_after', 'after', None),
        ('swarm_after', 'after', None),
        ('member_after', 'after', 'swarm_member_failed'),
        ('swarm_after', 'after', 'swarm_member_failed'),
        ('member_after', 'after', 'operator stop'),
        ('swarm_after', 'after', 'operator stop'),
    ]
