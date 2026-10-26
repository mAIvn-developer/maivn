"""Characterize Client configuration, interrupt answers and the local tool stream pump."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from maivn_contracts.runtime import SingleChoiceInterruptResponse
from pydantic import AnyUrl

from maivn import (
    Agent,
    Client,
    ClientConfig,
    InMemoryPrivateDataStore,
    LocalVaultConfig,
    RunOptions,
    depends_on_interrupt,
)

if TYPE_CHECKING:
    from collections.abc import Callable

JsonObject = dict[str, Any]
_SESSION = 'ses-char'


def _client(handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _body(request: httpx.Request) -> JsonObject:
    return cast('JsonObject', json.loads(request.content.decode('utf-8')))


def _envelope(event_type: str, payload: JsonObject, ordinal: int) -> JsonObject:
    return {
        'event_id': f'evt-{ordinal}',
        'ordinal': f'ord-{ordinal}',
        'type': event_type,
        'session_id': _SESSION,
        'root_event_id': 'evt-root',
        'actor': 'invoke',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': payload,
        'ts': '2026-07-04T12:00:00Z',
    }


def _final(ordinal: int) -> JsonObject:
    return _envelope(
        'final',
        {
            'message': {
                'message_id': 'msg-final',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-07-04T12:00:01Z',
            },
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        },
        ordinal,
    )


def _tool_start(tool_call: object, ordinal: int, tool_name: str = 'tool') -> JsonObject:
    return _envelope(
        'system_tool_start',
        {'stage': 'tool_start', 'tool_name': tool_name, 'tool_call': tool_call},
        ordinal,
    )


def _phase_started(ordinal: int) -> JsonObject:
    return _envelope('system_tool_chunk', {'stage': 'tool_execution', 'phase': 'started'}, ordinal)


def _call(
    tool_id: str, *, namespace: str = 'sdk', arguments: JsonObject | None = None
) -> JsonObject:
    return {
        'call_id': f'call-{tool_id}',
        'spec_ref': {'tool_id': tool_id, 'namespace': namespace, 'version': 'v1'},
        'arguments': arguments or {},
        'lineage': {'session_id': _SESSION, 'invocation_id': 'inv-char'},
    }


def _sse(events: list[JsonObject]) -> httpx.Response:
    content = ''.join(
        f'id: {index}\nevent: {event["type"]}\ndata: {json.dumps(event)}\n\n'
        for index, event in enumerate(events, start=1)
    )
    return httpx.Response(
        HTTPStatus.OK, headers={'content-type': 'text/event-stream'}, content=content
    )


class _Server:
    """Record every request and serve one invoke followed by a fixed SSE script."""

    def __init__(self, events: list[JsonObject]) -> None:
        self.events = events
        self.requests: list[tuple[str, str, JsonObject | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = _body(request) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': _SESSION, 'stream_position': 0}
            )
        if request.url.path == f'/v1/sessions/{_SESSION}/events':
            return _sse(self.events)
        if request.url.path == f'/v1/sessions/{_SESSION}/tools/results':
            return httpx.Response(HTTPStatus.ACCEPTED, json={})
        if '/interrupts/' in request.url.path:
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'thread_id': 'thr-char', 'session_id': _SESSION, 'stream_position': 0},
            )
        return httpx.Response(HTTPStatus.NOT_FOUND, json={'detail': 'unexpected'})

    def paths(self) -> list[str]:
        return [path for _method, path, _body in self.requests]

    def tool_results(self) -> list[JsonObject]:
        return [
            body
            for _method, path, body in self.requests
            if path.endswith('/tools/results') and body is not None
        ]


# --- configuration -----------------------------------------------------------------


def test_client_exposes_v1_execution_settings_from_overrides() -> None:
    """Constructor overrides land in config and the v1 accessor properties."""
    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        timeout=7,
        thread_id='thr-default',
        tool_execution_timeout=1.5,
        dependency_wait_timeout=2.5,
        total_execution_timeout=3.5,
        client_timezone='UTC',
    )
    assert (client.timeout, client.timeout_seconds) == (7, 7)
    assert client.thread_id == 'thr-default'
    assert client.tool_execution_timeout == 1.5  # noqa: PLR2004 - echoed override.
    assert client.dependency_wait_timeout == 2.5  # noqa: PLR2004 - echoed override.
    assert client.total_execution_timeout == 3.5  # noqa: PLR2004 - echoed override.
    assert client.client_timezone == 'UTC'


def test_local_vault_cannot_combine_with_an_explicit_store() -> None:
    """A local vault configuration is refused next to an explicit private store."""
    with pytest.raises(
        ValueError, match=r'^local_vault cannot be combined with an explicit private_data_store\.$'
    ):
        _client(
            lambda _request: httpx.Response(HTTPStatus.OK),
            private_data_store=InMemoryPrivateDataStore(),
            local_vault=LocalVaultConfig(),
        )


@pytest.mark.parametrize(
    ('detector', 'expected'),
    [
        (lambda: 'Europe/Paris', 'Europe/Paris'),
        (lambda: '   ', None),
        (lambda: 5, None),
        (None, None),
        ('not-callable', None),
    ],
)
def test_timezone_detection_uses_an_optional_tzlocal_detector(
    monkeypatch: pytest.MonkeyPatch, detector: object, expected: str | None
) -> None:
    """Only a callable detector returning a non-blank string sets the timezone."""
    module = types.ModuleType('tzlocal')
    if detector is not None:
        setattr(module, 'get_localzone_name', detector)  # noqa: B010 - dynamic module export.
    monkeypatch.setitem(sys.modules, 'tzlocal', module)
    client = Client(config=ClientConfig(api_key='k', base_url=AnyUrl('http://testserver')))
    assert client.client_timezone == expected
    no_detection = Client(
        config=ClientConfig(api_key='k', base_url=AnyUrl('http://testserver')),
        auto_detect_timezone=False,
    )
    assert no_detection.client_timezone is None


def test_timezone_detector_failure_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising detector leaves the timezone unset instead of failing construction."""
    module = types.ModuleType('tzlocal')

    def broken() -> str:
        message = 'no zoneinfo'
        raise OSError(message)

    setattr(module, 'get_localzone_name', broken)  # noqa: B010 - dynamic module export.
    monkeypatch.setitem(sys.modules, 'tzlocal', module)
    client = Client(config=ClientConfig(api_key='k', base_url=AnyUrl('http://testserver')))
    assert client.client_timezone is None


def test_forget_private_data_validates_and_clears_an_explicit_store() -> None:
    """A blank thread id is refused; an explicit store forgets the thread's pairs."""
    store = InMemoryPrivateDataStore()
    asyncio.run(store.remember('thr-1', {'account': 'ACC-1'}))
    client = _client(lambda _request: httpx.Response(HTTPStatus.OK), private_data_store=store)
    with pytest.raises(ValueError, match=r'^thread_id must be a non-empty string\.$'):
        client.forget_private_data('   ')
    assert asyncio.run(store.resolve('thr-1', ())) == {'account': 'ACC-1'}
    client.forget_private_data('thr-1')
    assert asyncio.run(store.resolve('thr-1', ())) == {}


def test_forget_private_data_without_a_store_sends_nothing() -> None:
    """With persistence disabled forgetting is a local no-op with no request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(HTTPStatus.OK)

    _client(handler, private_data_store=None).forget_private_data('thr-1')
    assert seen == []


# --- interrupt answers -------------------------------------------------------------


@pytest.mark.parametrize(
    ('answer', 'expected'),
    [
        ('text', {'kind': 'free_text', 'value': 'text'}),
        (True, {'kind': 'boolean', 'value': True}),
        (['a', 'b'], {'kind': 'multiple_choice', 'value': ['a', 'b']}),
        ({'k': 1}, {'kind': 'structured', 'value': {'k': 1}}),
        (
            SingleChoiceInterruptResponse(kind='single_choice', value='typed'),
            {'kind': 'single_choice', 'value': 'typed'},
        ),
    ],
)
def test_interrupt_answers_are_wrapped_in_typed_envelopes(
    answer: object, expected: JsonObject
) -> None:
    """Python answers map to typed envelopes; typed envelopes pass through."""
    seen: list[tuple[str, JsonObject]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, _body(request)))
        return httpx.Response(
            HTTPStatus.ACCEPTED,
            json={'thread_id': 'thr-1', 'session_id': 'ses-1', 'stream_position': 0},
        )

    _client(handler).submit_interrupt_response('thr-1', 'cp-1', answer=answer, responded_by='usr-1')
    assert seen == [
        (
            '/v1/threads/thr-1/interrupts/cp-1/responses',
            {'answer': expected, 'responded_by': 'usr-1', 'surface': 'sdk'},
        )
    ]


@pytest.mark.parametrize('answer', [[1, 2], 3, None])
def test_unsupported_interrupt_answers_are_refused_locally(answer: object) -> None:
    """Non-string lists and other values raise before any request is sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(HTTPStatus.ACCEPTED, json={})

    with pytest.raises(
        TypeError,
        match=r'^interrupt answer must be text, boolean, string list, object, or typed response$',
    ):
        _client(handler).submit_interrupt_response(
            'thr-1', 'cp-1', answer=answer, responded_by='usr-1'
        )
    assert seen == []


# --- thread events -----------------------------------------------------------------


def test_sync_thread_events_streams_the_thread_route() -> None:
    """The synchronous thread event iterator reads the thread events route."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _sse([_final(1)])

    events = list(_client(handler).thread_events('thr-1'))
    assert [event.event_type for event in events] == ['final']
    assert seen == ['/v1/threads/thr-1/events']


# --- local tool stream pump --------------------------------------------------------


def _stream_types(agent: Agent) -> list[str]:
    return [event.event_type for event in agent.stream('go', options=RunOptions(thread_id='t'))]


def test_hosted_and_malformed_tool_starts_pass_through_without_result_uploads() -> None:
    """Non-SDK and malformed tool starts are surfaced but never uploaded as results."""
    server = _Server(
        [
            _tool_start(_call('web_search', namespace='hosted'), 1, 'web_search'),
            _tool_start('not-a-call', 2),
            _phase_started(3),
            _final(4),
        ]
    )
    agent = Agent(name='pump', client=_client(server))
    # Tool starts are re-queued by their execution tasks, so only membership is stable.
    assert sorted(_stream_types(agent)) == [
        'final',
        'system_tool_chunk',
        'system_tool_start',
        'system_tool_start',
    ]
    assert server.tool_results() == []


def test_unknown_sdk_tool_uploads_an_error_and_surfaces_its_start() -> None:
    """An unregistered SDK call uploads unknown_sdk_tool and yields its start and error."""
    server = _Server([_tool_start(_call('ghost'), 1, 'ghost'), _phase_started(2), _final(3)])
    agent = Agent(name='pump', client=_client(server))
    assert sorted(_stream_types(agent)) == [
        'final',
        'system_tool_chunk',
        'system_tool_error',
        'system_tool_start',
    ]
    (batch,) = server.tool_results()
    (outcome,) = batch['outcomes']
    assert outcome['call_id'] == 'call-ghost'
    assert outcome['status'] == 'error'
    assert outcome['error']['code'] == 'unknown_sdk_tool'
    assert outcome['error']['message'] == 'SDK tool is not registered: ghost'


def _interrupt(**fields: object) -> JsonObject:
    interrupt: JsonObject = {
        'kind': 'tool_argument',
        'checkpoint_id': 'cp-ticket',
        'thread_id': 'thr-char',
        'session_id': _SESSION,
        'tool_name': 'deploy',
        'argument_name': 'ticket',
        'question': 'Ticket id? ',
    }
    interrupt.update(fields)
    return {key: value for key, value in interrupt.items() if value is not None}


def _interrupt_agent(server: _Server, prompts: list[str]) -> Agent:
    agent = Agent(name='deployer', client=_client(server))

    def handler(prompt: str) -> str:
        prompts.append(prompt)
        return 'DEP-1'

    @agent.toolify()
    @depends_on_interrupt(arg_name='ticket', input_handler=handler, prompt='Ticket: ')
    def deploy(service: str, ticket: str) -> str:
        return f'{service}:{ticket}'

    _ = deploy
    return agent


def test_canonical_interrupt_nested_in_a_stream_part_is_answered() -> None:
    """A tool-argument interrupt inside stream_part.data is collected and submitted."""
    server = _Server(
        [
            _envelope(
                'interrupt_required',
                {'stream_part': {'data': {'interrupt': _interrupt()}}},
                1,
            ),
            _final(2),
        ]
    )
    prompts: list[str] = []
    agent = _interrupt_agent(server, prompts)
    assert _stream_types(agent) == ['interrupt_required', 'final']
    assert prompts == ['Ticket id? ']
    answers = [
        (path, body)
        for _method, path, body in server.requests
        if path == '/v1/threads/thr-char/interrupts/cp-ticket/responses'
    ]
    assert answers == [
        (
            '/v1/threads/thr-char/interrupts/cp-ticket/responses',
            {
                'answer': {'kind': 'free_text', 'value': 'DEP-1'},
                'responded_by': 'sdk-user',
                'surface': 'sdk',
            },
        )
    ]


@pytest.mark.parametrize(
    'interrupt',
    [
        pytest.param(_interrupt(checkpoint_id=''), id='blank-checkpoint'),
        pytest.param(_interrupt(thread_id=None), id='missing-thread'),
        pytest.param(_interrupt(tool_name='other_tool'), id='unknown-tool'),
        pytest.param(_interrupt(argument_name='service'), id='not-an-interrupt-argument'),
        pytest.param(_interrupt(kind='agent_followup'), id='other-kind'),
    ],
)
def test_unusable_canonical_interrupts_are_not_answered(interrupt: JsonObject) -> None:
    """Incomplete or unmatched tool-argument interrupts never prompt or submit."""
    server = _Server([_envelope('interrupt_required', {'interrupt': interrupt}, 1), _final(2)])
    prompts: list[str] = []
    agent = _interrupt_agent(server, prompts)
    assert _stream_types(agent) == ['interrupt_required', 'final']
    assert prompts == []
    assert not any('/interrupts/' in path for path in server.paths())
