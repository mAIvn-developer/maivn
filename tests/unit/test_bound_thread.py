"""Bound thread handles preserve an owner's invocation behavior and identity."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, Swarm
from maivn._internal.compat.options import PlanningChoice
from maivn._internal.models import RunOptions, StreamEvent
from maivn._internal.thread import BoundThread

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def _event(position: int) -> StreamEvent:
    return StreamEvent(position=position, event_type='progress', data={'payload': {}})


class _Owner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, RunOptions | None]] = []
        self.closed = False

    def invoke(self, messages: object, *, options: RunOptions | None, **_kwargs: object) -> object:
        self.calls.append((str(messages), options))
        return {'messages': messages, 'thread_id': None if options is None else options.thread_id}

    async def ainvoke(
        self, messages: object, *, options: RunOptions | None, **_kwargs: object
    ) -> object:
        return self.invoke(messages, options=options)

    def stream(
        self, messages: object, *, options: RunOptions | None, **_kwargs: object
    ) -> Iterator[StreamEvent]:
        self.calls.append((str(messages), options))
        try:
            if messages == 'fail':
                message = 'source failed'
                raise RuntimeError(message)
            if messages == 'short':
                yield _event(2)
            else:
                yield _event(4)
                yield _event(6)
        finally:
            self.closed = True

    async def astream(
        self, messages: object, *, options: RunOptions | None, **_kwargs: object
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append((str(messages), options))
        try:
            yield _event(8)
        finally:
            self.closed = True

    def thread_events(self, _thread_id: str, *, options: RunOptions) -> Iterator[StreamEvent]:
        yield _event(options.resume_from_position + 1)

    async def athread_events(
        self, _thread_id: str, *, options: RunOptions
    ) -> AsyncIterator[StreamEvent]:
        yield _event(options.resume_from_position + 1)

    def get_thread(self, thread_id: str) -> object:
        return {'thread_id': thread_id}

    async def aget_thread(self, thread_id: str) -> object:
        return self.get_thread(thread_id)


def test_bound_thread_uses_one_fixed_id_and_preserves_call_options() -> None:
    """Repeated sends retain the supplied identity without replacing explicit tuning."""
    owner = _Owner()
    thread = BoundThread(owner, 'thr-fixed', options=RunOptions(model='fast'))

    assert thread.send(['first', 'second'], options=RunOptions(reasoning='high')) == {
        'messages': ['first', 'second'],
        'thread_id': 'thr-fixed',
    }
    assert cast('dict[str, object]', thread.send('third'))['thread_id'] == 'thr-fixed'
    first_options = owner.calls[0][1]
    second_options = owner.calls[1][1]
    assert isinstance(first_options, RunOptions)
    assert isinstance(second_options, RunOptions)
    assert [first_options.thread_id, second_options.thread_id] == ['thr-fixed', 'thr-fixed']
    assert first_options.model == 'fast'
    assert first_options.reasoning == 'high'


def test_bound_thread_rejects_conflicting_thread_options_and_kwargs() -> None:
    """A handle cannot be redirected by construction or an individual call."""
    owner = _Owner()
    with pytest.raises(ValueError, match='conflicts'):
        BoundThread(owner, 'thr-a', options=RunOptions(thread_id='thr-b'))

    thread = BoundThread(owner, options=RunOptions(thread_id='thr-options'))
    assert thread.thread_id == 'thr-options'
    with pytest.raises(ValueError, match='bound thread'):
        thread.send('hello', options=RunOptions(thread_id='thr-other'))
    with pytest.raises(ValueError, match='thread_id'):
        thread.send('hello', thread_id='thr-other')
    with pytest.raises(ValueError, match='blank'):
        BoundThread(owner, '')


def test_bound_thread_stream_updates_cursor_and_closes_source() -> None:
    """A closed sync wrapper releases its source and resumes from the latest position."""
    first_position = 4
    resumed_position = 5
    owner = _Owner()
    thread = BoundThread(owner, 'thr-stream')
    stream = thread.stream('hello')
    assert next(stream).position == first_position
    stream.close()

    assert owner.closed is True
    assert thread.resume_from_position == first_position
    assert [event.position for event in thread.events()] == [resumed_position]
    assert thread.resume_from_position == resumed_position


def test_bound_thread_allows_explicit_cursor_reset_and_state_delegation() -> None:
    """Replay can reset a retained cursor without altering the thread identity."""
    reset_position = 2
    next_position = 3
    owner = _Owner()
    thread = BoundThread(owner, 'thr-state', options=RunOptions(resume_from_position=9))

    events = thread.events(options=RunOptions(resume_from_position=reset_position))
    assert [event.position for event in events] == [next_position]
    assert thread.resume_from_position == next_position
    assert thread.state() == {'thread_id': 'thr-state'}


def test_bound_thread_merges_current_owner_defaults_and_typed_option_overrides() -> None:
    """Scope mode leaves absent options alone and keeps nested explicit options typed."""
    owner = _Owner()
    thread = BoundThread(owner, 'thr-defaults', pass_thread_id=True)

    thread.send('default')
    thread.send('override', options=RunOptions(planning=PlanningChoice(tier='standard')))

    default_options = owner.calls[0][1]
    override_options = owner.calls[1][1]
    assert default_options is None
    assert isinstance(override_options, RunOptions)
    assert isinstance(override_options.planning, PlanningChoice)
    assert override_options.planning.tier == 'standard'


def test_invalid_event_options_do_not_reset_the_retained_cursor() -> None:
    """An invalid replay request leaves the handle's previously observed position unchanged."""
    retained_position = 9
    owner = _Owner()
    thread = BoundThread(
        owner,
        'thr-cursor',
        options=RunOptions(resume_from_position=retained_position),
    )

    with pytest.raises(ValueError, match='conflicts'):
        list(thread.events(options=RunOptions(thread_id='thr-other', resume_from_position=2)))

    assert thread.resume_from_position == retained_position


def test_new_bound_turn_resets_cursor_before_a_shorter_stream() -> None:
    """A new active session cannot inherit a larger cursor from an earlier turn."""
    first_turn_positions = [4, 6]
    first_turn_end = 6
    second_turn_positions = [2]
    second_turn_end = 2
    owner = _Owner()
    thread = BoundThread(owner, 'thr-turns')

    assert [event.position for event in thread.stream('long')] == first_turn_positions
    assert thread.resume_from_position == first_turn_end
    assert [event.position for event in thread.stream('short')] == second_turn_positions
    assert thread.resume_from_position == second_turn_end


@pytest.mark.parametrize('pass_thread_id', [False, True])
def test_new_bound_turn_does_not_forward_an_old_replay_cursor(*, pass_thread_id: bool) -> None:
    """Replay options cannot skip the beginning of a new Client or scope invocation."""
    owner = _Owner()
    thread = BoundThread(
        owner,
        'thr-turn-cursor',
        options=RunOptions(resume_from_position=12),
        pass_thread_id=pass_thread_id,
    )

    assert [event.position for event in thread.stream('short')] == [2]
    forwarded_options = owner.calls[0][1]
    assert forwarded_options is not None
    assert forwarded_options.resume_from_position == 0


def test_bound_thread_releases_a_failing_stream_source() -> None:
    """A stream error closes the source and leaves the handle usable for a later turn."""
    owner = _Owner()
    thread = BoundThread(owner, 'thr-failure')

    with pytest.raises(RuntimeError, match='source failed'):
        list(thread.stream('fail'))

    assert owner.closed is True
    assert [event.position for event in thread.stream('short')] == [2]


def test_bound_thread_async_methods_preserve_identity_and_close_source() -> None:
    """Async sends and streams follow the same fixed-id and cleanup rules."""

    async def exercise() -> None:
        stream_position = 8
        replay_position = 9
        owner = _Owner()
        thread = BoundThread(owner, 'thr-async')
        assert await thread.asend('hello') == {'messages': 'hello', 'thread_id': 'thr-async'}
        stream = thread.astream('again')
        assert (await anext(stream)).position == stream_position
        await stream.aclose()
        assert owner.closed is True
        assert await thread.astate() == {'thread_id': 'thr-async'}
        assert [event.position async for event in thread.aevents()] == [replay_position]
        assert thread.resume_from_position == replay_position

    asyncio.run(exercise())


def test_bound_thread_generation_is_local_and_has_no_owner_side_effect() -> None:
    """Constructing without an id selects an immutable local id and starts no request."""
    owner = _Owner()
    thread = BoundThread(owner)

    assert thread.thread_id.startswith('thr-')
    assert not owner.calls


def test_bound_thread_send_keeps_full_messages_on_the_normal_client_invoke_path() -> None:
    """Bound sends use the ordinary invoke route, so no message or tool behavior is lost."""
    request_bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            request_bodies.append(cast('dict[str, object]', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-bound', 'stream_position': 0},
            )
        payload = {
            'event_id': 'evt-bound',
            'ordinal': 'ord-bound',
            'type': 'final',
            'session_id': 'ses-bound',
            'root_event_id': 'evt-root',
            'payload': {
                'message': {
                    'message_id': 'msg-bound',
                    'role': 'assistant',
                    'content': 'done',
                    'ts': '2026-09-16T12:00:00Z',
                },
                'usage': {'input_tokens': 1, 'output_tokens': 1},
            },
            'ts': '2026-09-16T12:00:00Z',
        }
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=f'id: 1\nevent: final\ndata: {json.dumps(payload)}\n\n',
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    response = BoundThread(client, 'thr-bound').send(['first', 'second'])

    assert response.final_message.content == 'done'
    assert len(request_bodies) == 1
    body = request_bodies[0]
    messages = cast('list[dict[str, object]]', body['messages'])
    assert [message['content'] for message in messages] == ['first', 'second']
    assert body['run_config'] == {'thread_id': 'thr-bound', 'user_id': 'sdk-user'}
    assert body['model'] == 'auto'
    assert body['stream_deltas'] is False


@pytest.mark.parametrize('scope_type', [None, Agent, Swarm])
@pytest.mark.parametrize('is_async', [False, True])
def test_public_bound_thread_replays_the_observed_session_after_early_stream_close(
    scope_type: type[Agent | Swarm] | None,
    *,
    is_async: bool,
) -> None:
    """Replay stays on the invoke session before a durable thread row exists."""
    initial_position = 4
    nested_position = 5
    final_position = 6
    session_requests: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-root', 'stream_position': 0},
            )
        if request.url.path != '/v1/sessions/ses-root/events':
            pytest.fail(f'unexpected replay path: {request.url.path}')
        cursor = request.url.params.get('from_position')
        session_requests.append((request.url.path, cursor))
        if cursor is None:
            return _stream_event_response(position=initial_position, session_id='ses-root')
        if cursor == str(initial_position):
            return _stream_event_response(
                position=nested_position,
                session_id='ses-child',
                parent_session_id='ses-root',
            )
        if cursor == str(nested_position):
            return _stream_event_response(
                position=final_position,
                session_id='ses-root',
                event_type='final',
            )
        pytest.fail(f'unexpected resume cursor: {cursor}')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    owner = client if scope_type is None else scope_type(name='replay-owner', client=client)
    thread = owner.thread('thr-session-pinned')

    async def consume() -> None:
        stream = thread.astream('start')
        assert (await anext(stream)).position == initial_position
        await stream.aclose()
        assert [event.position async for event in thread.aevents()] == [nested_position]
        assert [event.position async for event in thread.aevents()] == [final_position]

    if is_async:
        asyncio.run(consume())
    else:
        stream = thread.stream('start')
        assert next(stream).position == initial_position
        stream.close()
        assert [event.position for event in thread.events()] == [nested_position]
        assert [event.position for event in thread.events()] == [final_position]
    assert session_requests == [
        ('/v1/sessions/ses-root/events', None),
        ('/v1/sessions/ses-root/events', str(initial_position)),
        ('/v1/sessions/ses-root/events', str(nested_position)),
    ]


@pytest.mark.parametrize('scope_type', [Agent, Swarm])
@pytest.mark.parametrize('scope_model', ['ultra', 'configured-model'])
@pytest.mark.parametrize('options', [None, RunOptions(planning=PlanningChoice(tier='standard'))])
def test_public_scope_threads_keep_defaults_messages_tools_and_hooks(
    scope_type: type[Agent | Swarm],
    scope_model: str,
    options: RunOptions | None,
) -> None:
    """Scope handles retain model/tool/hook behavior while passing full message lists."""
    request_bodies: list[dict[str, object]] = []
    hook_stages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            request_bodies.append(cast('dict[str, object]', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-scope-bound', 'stream_position': 0},
            )
        return _final_sse_response('ses-scope-bound')

    def before(_payload: object) -> None:
        hook_stages.append('before')

    def after(_payload: object) -> None:
        hook_stages.append('after')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    scope = scope_type(
        name='bound-scope',
        client=client,
        model=scope_model,
        before_execute=before,
        after_execute=after,
        hook_execution_mode='scope',
    )
    if isinstance(scope, Agent):

        def lookup() -> dict[str, str]:
            return {'status': 'ok'}

        scope.add_tool(lookup, name='lookup')

    baseline = scope.invoke(
        ['first', 'second'],
        options=options,
    )
    response = scope.thread('thr-scope').send(['first', 'second'], options=options)

    assert baseline.final_message.content == 'done'
    assert response.final_message.content == 'done'
    assert hook_stages == ['before', 'after', 'before', 'after']
    expected_request_count = 2
    assert len(request_bodies) == expected_request_count
    direct_body, bound_body = request_bodies
    for body in (direct_body, bound_body):
        messages = cast('list[dict[str, object]]', body['messages'])
        assert [message['content'] for message in messages] == ['first', 'second']
        run_config = cast('dict[str, object]', body['run_config'])
        if options is not None:
            assert run_config['planning'] == {'tier': 'standard'}
    assert bound_body['model'] == direct_body['model']
    assert bound_body.get('model_directive') == direct_body.get('model_directive')
    direct_config = cast('dict[str, object]', direct_body['run_config'])
    bound_config = cast('dict[str, object]', bound_body['run_config'])
    assert direct_config | {'thread_id': 'thr-scope'} == bound_config
    if isinstance(scope, Agent):
        direct_tools = cast('list[dict[str, object]]', direct_body['tools'])
        bound_tools = cast('list[dict[str, object]]', bound_body['tools'])
        assert [tool['name'] for tool in direct_tools] == ['lookup']
        assert bound_tools == direct_tools


def _final_sse_response(session_id: str) -> httpx.Response:
    """Return one minimal completed stream for a scoped invoke."""
    payload = {
        'event_id': 'evt-scope-bound',
        'ordinal': 'ord-scope-bound',
        'type': 'final',
        'session_id': session_id,
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-scope-bound',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-09-16T12:00:00Z',
            },
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        },
        'ts': '2026-09-16T12:00:00Z',
    }
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: 1\nevent: final\ndata: {json.dumps(payload)}\n\n',
    )


def _stream_event_response(
    *,
    position: int,
    session_id: str,
    parent_session_id: str | None = None,
    event_type: str = 'progress',
) -> httpx.Response:
    """Return one canonical SSE event with optional nested-session lineage."""
    payload: dict[str, object] = {
        'event_id': f'evt-{position}',
        'ordinal': f'ord-{position}',
        'type': event_type,
        'session_id': session_id,
        'root_event_id': 'evt-root',
        'payload': {},
        'ts': '2026-09-16T12:00:00Z',
    }
    if parent_session_id is not None:
        payload['parent_session_id'] = parent_session_id
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: {position}\nevent: {event_type}\ndata: {json.dumps(payload)}\n\n',
    )
