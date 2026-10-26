"""Characterization tests for the v1-compatible invocation helpers.

``EventInvocationBuilder`` is what ``Agent.events(...)`` returns; it is bound
here to a scripted scope so no client or network is involved.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from maivn import InvokeResponse, MaivnSDKError, StreamEvent
from maivn._internal.compat.invocation import (
    EventInvocationBuilder,
    StructuredOutputInvocationBuilder,
    resolve_max_concurrency,
    response_from_stream_events,
    run_abatch,
    run_batch,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterator

_NO_EVENTS = 'events invocation produced no events'


def _event(position: int, event_type: str, **data: object) -> StreamEvent:
    return StreamEvent(position=position, event_type=event_type, data=data)


_FINAL = _event(9, 'final', payload={'content': 'done', 'session_id': 'ses-1'})


class _SyncScope:
    """Scope double exposing only a synchronous stream."""

    def __init__(self, events: list[StreamEvent]) -> None:
        self.events = events
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def stream(self, *args: object, **kwargs: object) -> Iterator[StreamEvent]:
        self.calls.append((args, kwargs))
        yield from self.events


class _AsyncScope(_SyncScope):
    """Scope double exposing an async stream as well."""

    def __init__(self, events: list[StreamEvent]) -> None:
        super().__init__(events)
        self.closed = False

    async def astream(self, *args: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        self.calls.append((args, kwargs))
        try:
            for event in self.events:
                yield event
        finally:
            self.closed = True


def _routing_events() -> list[StreamEvent]:
    return [
        _event(1, 'assignment_received', payload={'agent': 'a'}, session_id='ses-1'),
        _event(2, 'enrichment', payload={'phase': 'memory', 'items': 2}),
        _event(3, 'system_tool_chunk', payload={'phase': ''}),
        _event(4, 'tool_event', type='tool_call', payload={'category': 'audit'}),
        _event(5, 'progress', category='custom'),
        _FINAL,
    ]


def test_events_builder_projects_every_event_payload_shape() -> None:
    """Session starts, enrichment phases and plain events get their v1 payloads."""
    received: list[dict[str, object]] = []
    scope = _SyncScope(_routing_events())
    builder = EventInvocationBuilder(scope, on_event=received.append)
    events = list(builder.stream('hi'))
    assert [event.position for event in events] == [1, 2, 3, 4, 5, 9]
    assert received == [
        {'event': 'session_start', 'payload': {'agent': 'a', 'session_id': 'ses-1'}},
        {'event': 'enrichment', 'payload': {'phase': 'memory', 'items': 2}},
        {'payload': {'phase': ''}, 'category': 'system_tool_chunk'},
        {'type': 'tool_call', 'payload': {'category': 'audit'}, 'category': 'tool_event'},
        {'category': 'custom'},
        {'payload': {'content': 'done', 'session_id': 'ses-1'}, 'category': 'final'},
    ]
    assert scope.calls == [(('hi',), {'verbose': True})]


def test_assignment_without_session_id_keeps_the_payload_alone() -> None:
    """A session start without a session id forwards only its payload."""
    received: list[dict[str, object]] = []
    events = [_event(1, 'assignment_received', session_id=''), _FINAL]
    _ = list(EventInvocationBuilder(_SyncScope(events), on_event=received.append).stream())
    assert received[0] == {'event': 'session_start', 'payload': {}}


def test_events_builder_filters_on_any_event_category() -> None:
    """Include/exclude match the event type, ``data.type`` or ``payload.category``."""
    received: list[dict[str, object]] = []
    builder = EventInvocationBuilder(
        _SyncScope(_routing_events()),
        include=['tool_call', 'enrichment', 'final'],
        exclude='audit',
        on_event=received.append,
        auto_verbose=False,
    )
    _ = list(builder.stream())
    assert [payload.get('event', payload.get('category')) for payload in received] == [
        'enrichment',
        'final',
    ]
    single = EventInvocationBuilder(
        _SyncScope(_routing_events()),
        include='audit',
        on_event=received.append,
    )
    received.clear()
    _ = list(single.stream())
    assert [payload['category'] for payload in received] == ['tool_event']


def test_events_builder_respects_an_explicit_verbose_and_auto_verbose_off() -> None:
    """``verbose`` is only added when auto_verbose is on and the caller did not pass it."""
    scope = _SyncScope([_FINAL])
    _ = list(EventInvocationBuilder(scope).stream(verbose=False))
    _ = list(EventInvocationBuilder(scope, auto_verbose=False).stream())
    assert scope.calls == [((), {'verbose': False}), ((), {})]


def test_events_builder_invoke_projects_the_final_event() -> None:
    """``invoke`` consumes the stream and returns the projected response."""
    response = EventInvocationBuilder(_SyncScope(_routing_events())).invoke('hi')
    assert isinstance(response, InvokeResponse)
    assert response.response == 'done'
    assert response.session_id == 'ses-1'
    assert response.event_positions == [1, 2, 3, 4, 5, 9]


def test_events_builder_refuses_an_empty_stream() -> None:
    """Sync and async invokes refuse a stream with no events."""
    builder = EventInvocationBuilder(_SyncScope([]))
    with pytest.raises(ValueError, match=f'^{_NO_EVENTS}$'):
        _ = builder.invoke()
    with pytest.raises(ValueError, match=f'^{_NO_EVENTS}$'):
        _ = asyncio.run(builder.ainvoke())


def test_events_builder_async_stream_routes_and_closes() -> None:
    """With a native async stream, events are routed and the source is closed."""
    received: list[dict[str, object]] = []
    scope = _AsyncScope([_event(1, 'progress'), _FINAL])
    builder = EventInvocationBuilder(scope, include='progress', on_event=received.append)
    response = asyncio.run(builder.ainvoke('q'))
    assert response.response == 'done'
    assert received == [{'category': 'progress'}]
    assert scope.calls == [(('q',), {'verbose': True})]
    assert scope.closed is True


def test_events_builder_async_stream_closes_the_source_when_abandoned() -> None:
    """Closing the routed stream early closes the scope's async stream too."""
    scope = _AsyncScope([_event(1, 'progress'), _event(2, 'progress'), _FINAL])
    builder = EventInvocationBuilder(scope)

    async def first_then_close() -> tuple[int, bool]:
        stream = cast('AsyncGenerator[StreamEvent, None]', builder.astream())
        first = await anext(stream)
        await stream.aclose()
        return first.position, scope.closed

    assert asyncio.run(first_then_close()) == (1, True)


def test_events_builder_async_falls_back_to_the_sync_stream() -> None:
    """Without ``astream`` the sync stream runs in a worker thread."""
    received: list[dict[str, object]] = []
    scope = _SyncScope([_event(1, 'progress'), _FINAL])
    builder = EventInvocationBuilder(scope, on_event=received.append)

    async def collect() -> list[int]:
        return [event.position async for event in builder.astream('q')]

    assert asyncio.run(collect()) == [1, 9]
    assert scope.calls == [(('q',), {'verbose': True})]
    assert [payload['category'] for payload in received] == ['progress', 'final']


def test_events_builder_follow_up_configuration_keeps_routing() -> None:
    """``allow_followup_questions`` rebinds to the configured scope with the same filters."""
    received: list[dict[str, object]] = []
    configured = _SyncScope([_event(1, 'progress'), _FINAL])

    class Configurable(_SyncScope):
        def allow_followup_questions(self, **kwargs: object) -> _SyncScope:
            configured.calls.append(((), {'configured_with': kwargs}))
            return configured

    builder = EventInvocationBuilder(
        Configurable([]),
        include='progress',
        on_event=received.append,
        auto_verbose=False,
    ).allow_followup_questions(max_questions=2)
    _ = list(builder.stream())
    assert configured.calls == [((), {'configured_with': {'max_questions': 2}}), ((), {})]
    assert received == [{'category': 'progress'}]


def test_scope_methods_must_exist_and_be_callable() -> None:
    """Missing scope methods are attribute errors; non-callables are type errors."""

    class NoStream:
        pass

    class BadStream:
        stream = 'not callable'

    with pytest.raises(AttributeError, match=r'^Scope does not support stream\(\)\.$'):
        _ = list(EventInvocationBuilder(NoStream()).stream())
    with pytest.raises(TypeError, match=r'^Scope attribute stream is not callable\.$'):
        _ = list(EventInvocationBuilder(BadStream()).stream())


def test_response_projection_refuses_no_events() -> None:
    """Projecting an empty stream is refused."""
    with pytest.raises(ValueError, match=f'^{_NO_EVENTS}$'):
        _ = response_from_stream_events([])


def test_response_projection_without_a_terminal_uses_the_last_event() -> None:
    """With no terminal event the last event supplies the defaults-filled response."""
    response = response_from_stream_events(
        [
            _event(2, 'progress', content='early'),
            _event(3, 'progress', content='partial', tool_call_count=True, tool_names=['a', 1]),
        ],
    )
    assert response.model_dump(mode='json') == {
        'final_message': {
            'message_id': 'msg-event-3',
            'role': 'assistant',
            'content': 'partial',
            'attachments': None,
            'artifact_refs': None,
            'redaction': None,
            'metadata': None,
            'ts': '1970-01-01T00:00:00Z',
        },
        'session_id': 'ses-events',
        'organization_id': None,
        'project_id': None,
        'thread_id': None,
        'root_event_id': 'evt-events-root',
        'event_positions': [2, 3],
        'usage': {},
        'stop_reason': None,
        'tool_call_count': 0,
        'tool_names': ['a', '1'],
        'response': 'partial',
        'privacy_guidance': [],
        'result': None,
        'assistant_id': None,
        'artifacts': [],
    }


def test_response_projection_ignores_child_terminals_and_reads_fallback_fields() -> None:
    """A delegated child's final never ends the run; payload fields win over data."""
    events = [
        _event(1, 'final', parent_session_id='child', payload={'content': 'child'}),
        _event(
            2,
            'session.completed',
            thread_id='th-data',
            event_id='evt-2',
            payload={
                'final_message': 'not a mapping',
                'response': 'from response',
                'tool_calls': {'count': 4, 'names': ['x']},
                'usage': {'tokens': 5},
                'run_config': {'thread_id': 'th-config'},
                'stop_reason': 'end',
                'assistant_id': 'asst-1',
                'result': {'a': 1},
            },
        ),
        _event(3, 'final', parent_session_id='child-2'),
    ]
    response = response_from_stream_events(events)
    assert response.response == 'from response'
    assert response.thread_id == 'th-data'
    assert response.root_event_id == 'evt-2'
    assert response.tool_call_count == 4  # noqa: PLR2004
    assert response.tool_names == ['x']
    assert response.usage == {'tokens': 5}
    assert response.stop_reason == 'end'
    assert response.assistant_id == 'asst-1'
    assert response.result == {'a': 1}


def test_response_projection_reads_data_content_and_run_config_thread() -> None:
    """Content falls back to ``data.content``; thread id falls back to run_config."""
    event = _event(
        4,
        'final',
        content='data content',
        payload={'run_config': {'thread_id': 'th-config'}},
    )
    response = response_from_stream_events([event])
    assert response.response == 'data content'
    assert response.thread_id == 'th-config'


def test_repeated_tool_failure_without_a_tool_code_reports_the_terminal_code() -> None:
    """When no tool error carries a code, the terminal error code names the failure."""
    events = [
        _event(1, 'system_tool_error', payload={'outcome': {'error': {}}}),
        _event(2, 'system_tool_error', parent_session_id='child', payload={}),
        _event(3, 'error', payload={'error_code': 'tool_repeated_failure'}),
    ]
    with pytest.raises(MaivnSDKError, match=r'^tool_repeated_failure$'):
        _ = response_from_stream_events(events)


def test_repeated_tool_failure_reads_a_legacy_code_field() -> None:
    """The latest parent tool error's ``code`` field explains the repeated failure."""
    events = [
        _event(1, 'system_tool_error', outcome={'error': {'code': 'older_code'}}),
        _event(2, 'system_tool_error', outcome={'error': {'code': 'rate_limited'}}),
        _event(3, 'error', error_code='tool_repeated_failure', error_type='ToolError'),
    ]
    with pytest.raises(MaivnSDKError, match=r'^ToolError: rate_limited$'):
        _ = response_from_stream_events(events)


def test_resolve_max_concurrency_bounds() -> None:
    """Concurrency must be positive; it is capped by input count; no inputs means zero."""
    with pytest.raises(ValueError, match=r'^max_concurrency must be greater than 0$'):
        _ = resolve_max_concurrency(0, 3)
    assert resolve_max_concurrency(None, 0) == 0
    assert resolve_max_concurrency(5, 0) == 0
    assert resolve_max_concurrency(None, 3) is None
    assert resolve_max_concurrency(5, 3) == 3  # noqa: PLR2004
    assert resolve_max_concurrency(2, 3) == 2  # noqa: PLR2004
    assert resolve_max_concurrency(1, 1) == 1


class _BatchScope:
    """Scope double whose invokes echo their input."""

    def invoke(self, item: object, **kwargs: object) -> object:
        return (item, kwargs)

    async def ainvoke(self, item: object, **kwargs: object) -> object:
        await asyncio.sleep(0)
        return (item, kwargs)


def test_run_batch_preserves_input_order_and_forwards_kwargs() -> None:
    """Batch results line up with inputs; empty input skips the pool."""
    results = run_batch(
        _BatchScope(),
        iter(['a', 'b', 'c']),
        max_concurrency=2,
        invoke_kwargs={'thread_id': 't'},
    )
    assert results == [
        ('a', {'thread_id': 't'}),
        ('b', {'thread_id': 't'}),
        ('c', {'thread_id': 't'}),
    ]
    assert run_batch(object(), [], max_concurrency=None, invoke_kwargs={}) == []


def test_run_abatch_preserves_input_order_and_handles_empty_input() -> None:
    """Async batches line up with inputs; empty input returns at once."""
    results = asyncio.run(
        run_abatch(_BatchScope(), ['x', 'y'], max_concurrency=None, invoke_kwargs={'v': 1}),
    )
    assert results == [('x', {'v': 1}), ('y', {'v': 1})]
    assert asyncio.run(run_abatch(object(), [], max_concurrency=1, invoke_kwargs={})) == []


def test_run_batch_requires_an_invoke_method() -> None:
    """A scope without ``invoke`` fails with the shared attribute error."""
    with pytest.raises(AttributeError, match=r'^Scope does not support invoke\(\)\.$'):
        _ = run_batch(object(), ['a'], max_concurrency=None, invoke_kwargs={})


class _Answer(BaseModel):
    value: int


def test_structured_output_builder_forwards_the_model_to_every_entry_point() -> None:
    """invoke/stream/astream pass ``structured_output`` and caller options through."""
    calls: list[tuple[str, object, dict[str, object]]] = []

    class Scope:
        def invoke(self, messages: object, **kwargs: object) -> str:
            calls.append(('invoke', messages, kwargs))
            return 'invoked'

        async def ainvoke(self, messages: object, **kwargs: object) -> str:
            calls.append(('ainvoke', messages, kwargs))
            return 'ainvoked'

        def stream(self, messages: object, **kwargs: object) -> Iterator[StreamEvent]:
            calls.append(('stream', messages, kwargs))
            yield _FINAL

        async def astream(self, messages: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
            calls.append(('astream', messages, kwargs))
            yield _FINAL

        def allow_followup_questions(self, **kwargs: object) -> Scope:
            calls.append(('followups', None, kwargs))
            return self

    builder = StructuredOutputInvocationBuilder(Scope(), _Answer)
    assert builder.invoke('m', thread_id='t') == 'invoked'
    assert asyncio.run(builder.ainvoke('m')) == 'ainvoked'
    assert list(builder.stream('m', verbose=True)) == [_FINAL]

    async def collect() -> list[StreamEvent]:
        return [event async for event in builder.astream('m')]

    assert asyncio.run(collect()) == [_FINAL]
    rebound = builder.allow_followup_questions(max_questions=1)
    assert rebound.invoke('n') == 'invoked'
    names = [name for name, _messages, _kwargs in calls]
    assert names == ['invoke', 'ainvoke', 'stream', 'astream', 'followups', 'invoke']
    for name, _messages, kwargs in calls:
        if name != 'followups':
            assert kwargs['structured_output'] is _Answer
    invoke_kwargs = calls[0][2]
    assert invoke_kwargs == {
        'force_final_tool': False,
        'targeted_tools': None,
        'structured_output': _Answer,
        'thread_id': 't',
        'verbose': False,
        'reasoning': None,
        'metadata': None,
        'memory_config': None,
        'allow_private_in_system_tools': None,
        'model': None,
        'system_tools_config': None,
        'orchestration_config': None,
        'stream_response': False,
        'options': None,
        'origin': None,
    }
    assert calls[2][2] == {'structured_output': _Answer, 'verbose': True}
