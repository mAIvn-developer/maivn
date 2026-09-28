"""Unit tests for SDK HTTP wire mapping."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import (
    Agent,
    ApprovalDecision,
    Client,
    ClientConfig,
    InMemoryPrivateDataStore,
    MaivnSDKError,
    RunOptions,
    compose_argument_policy,
    depends_on_reevaluate,
    depends_on_tool,
    requires_producer_reference,
)
from maivn._internal.config import DEFAULT_TOOL_EXECUTION_TIMEOUT
from maivn._internal.models import ToolMetadata
from maivn._internal.reporting.context import current_reporter
from maivn._internal.wire import (
    _function_tool_spec,  # pyright: ignore[reportPrivateUsage] - private wire helper under test.
)
from maivn.messages import HumanMessage

if TYPE_CHECKING:
    from maivn._internal.reporting.terminal_reporter.base.reporter import BaseReporter

JsonObject: TypeAlias = dict[str, Any]
# httpx.MockTransport's handler parameter type (httpx._transports.mock.SyncHandler /
# AsyncHandler) is not re-exported from the top-level httpx module, so mirror its
# shape locally rather than reference the inaccessible attribute.  boundary
_SyncHandler: TypeAlias = Callable[[httpx.Request], httpx.Response]
_AsyncHandler: TypeAlias = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
_TOTAL_INVOKE_CALL_COUNT = 2
_STREAM_RESUME_POSITION = 3


class _SessionStartReporter:
    """Minimal reporter seam for asserting acceptance-time lifecycle reporting."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def report_session_start(self, session_id: str, assistant_id: str) -> None:
        self.calls.append((session_id, assistant_id))


def test_client_invoke_posts_once_and_builds_result_from_terminal_sse() -> None:
    """Client.invoke consumes SSE to completion and never polls the result route."""
    calls: list[tuple[str, str, JsonObject | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                _json_body(request),
                request.headers.get('authorization'),
            ),
        )
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-sdk', 'stream_position': 0},
            )
        assert request.url.path == '/v1/sessions/ses-sdk/events'
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 1\nevent: status_message_chunk\n'
                'data: {"event_id":"evt-1","ordinal":"ord-1",'
                '"type":"status_message_chunk","session_id":"ses-sdk",'
                '"root_event_id":"evt-root","payload":{"content_delta":"done"},'
                '"ts":"2026-07-04T12:00:00Z"}\n\n'
                'id: 2\nevent: final\n'
                'data: {"event_id":"evt-2","ordinal":"ord-2","type":"final",'
                '"session_id":"ses-sdk","root_event_id":"evt-root",'
                '"payload":{"message":{"message_id":"msg-final","role":"assistant",'
                '"content":"done","ts":"2026-07-04T12:00:00Z"},'
                '"assistant_id":"orchestrator_agent","usage":{"input_tokens":3,'
                '"output_tokens":2},"stop_reason":"end_turn",'
                '"tool_calls":{"count":0,"names":[]}},'
                '"ts":"2026-07-04T12:00:00Z"}\n\n'
            ),
        )

    client = _client(handler)

    response = client.invoke(
        [HumanMessage(message_id='msg-user', content='hello')],
        options=RunOptions(
            thread_id='thr-sdk',
            session_id='ses-sdk',
            user_id='usr-sdk',
            model='synthetic-model-id',
        ),
    )

    assert response.response == 'done'
    assert response.final_message.role == 'assistant'
    assert response.assistant_id == 'orchestrator_agent'
    assert response.usage == {'input_tokens': 3, 'output_tokens': 2}
    assert response.event_positions == [1, 2]
    assert calls[0][0] == 'POST'
    assert calls[0][1] == '/v1/invoke'
    assert calls[0][2] is not None
    assert calls[0][2]['run_config'] == {
        'user_id': 'usr-sdk',
        'thread_id': 'thr-sdk',
        'session_id': 'ses-sdk',
    }
    assert 'stream_response' not in calls[0][2]
    # A result-only invoke tells the server nobody watches live text deltas.
    assert calls[0][2]['stream_deltas'] is False
    assert calls[0][3] == 'Bearer test-key'
    assert calls[1][1] == '/v1/sessions/ses-sdk/events'
    assert all('/result' not in path for _method, path, _body, _auth in calls)
    assert len(calls) == _TOTAL_INVOKE_CALL_COUNT


def test_client_invoke_preserves_selected_artifact_refs_from_terminal_sse() -> None:
    """Selected immutable artifacts survive the SDK's streamed invoke projection."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-artifact-sdk', 'stream_position': 0},
            )
        return _artifact_invoke_sse_response()

    response = _client(handler).invoke('Create the report.')

    assert len(response.artifacts) == 1
    artifact = response.artifacts[0]
    assert artifact.custody == 'ordinary'
    assert artifact.artifact_id == 'artifact-sdk'
    assert response.final_message.artifact_refs == response.artifacts


def test_client_cancel_posts_to_the_owned_session_lifecycle_route() -> None:
    """Client.cancel exposes server-side cancellation as a public lifecycle operation."""
    calls: list[tuple[str, str, JsonObject | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, _json_body(request)))
        return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})

    client = _client(handler)

    client.cancel('ses-sdk-cancel')

    assert calls == [('POST', '/v1/sessions/ses-sdk-cancel/cancel', {})]


@pytest.mark.parametrize('code', ['provider_error', 'value_fill_exhausted'])
@pytest.mark.parametrize('mode', ['sync', 'async'])
def test_client_invoke_raises_terminal_sse_error(code: str, mode: str) -> None:
    """A terminal SSE error fails the invocation with the server message."""
    message = (
        'structured final value fill exhausted its repair budget'
        if code == 'value_fill_exhausted'
        else 'provider unavailable'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            assert body['stream_response'] is True
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-error', 'stream_position': 0},
            )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 1\nevent: error\n'
                'data: {"event_id":"evt-error","ordinal":"ord-1","type":"error",'
                '"session_id":"ses-error","root_event_id":"evt-root",'
                f'"payload":{{"stage":"error","error_type":"{code}","error_code":"{code}",'
                f'"message":"{message}","termination_reason":"error"}},'
                '"ts":"2026-07-04T12:00:00Z"}\n\n'
            ),
        )

    client = _client(handler)

    def invoke() -> None:
        if mode == 'sync':
            client.invoke('hello', options=RunOptions(stream_response=True))
        else:
            asyncio.run(client.ainvoke('hello', options=RunOptions(stream_response=True)))

    with pytest.raises(MaivnSDKError, match=message) as caught:
        invoke()
    assert caught.value.error_code == code
    assert caught.value.session_id == 'ses-error'
    assert caught.value.root_event_id == 'evt-root'


def test_client_stream_posts_then_reads_sse_with_resume_position() -> None:
    """Client.stream starts an invoke and resumes the SSE stream from a cursor."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-stream', 'stream_position': 0},
            )
        assert request.url.path == '/v1/sessions/ses-stream/events'
        assert request.url.params['from_position'] == str(_STREAM_RESUME_POSITION)
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 4\nevent: status_message_chunk\n'
                'data: {"event_id":"evt-4","ordinal":"ord-4","type":"status_message_chunk",'
                '"session_id":"ses-stream","root_event_id":"evt-root","trigger_chain_depth":0,'
                '"job_depth":0,"job_index":0,"actor":"agent:invoke","mode":"hosted",'
                '"payload":{"content_delta":"hi"},"ts":"2026-07-04T12:00:00Z"}\n\n'
                'id: 5\nevent: final\n'
                'data: {"event_id":"evt-5","ordinal":"ord-5","type":"final",'
                '"session_id":"ses-stream","root_event_id":"evt-root","trigger_chain_depth":0,'
                '"job_depth":0,"job_index":0,"actor":"agent:invoke","mode":"hosted",'
                '"payload":{},"ts":"2026-07-04T12:00:00Z"}\n\n'
            ),
        )

    client = _client(handler)

    reporter = _SessionStartReporter()
    reporter_token = current_reporter.set(cast('BaseReporter', reporter))
    try:
        events = list(
            client.stream(
                'hello',
                options=RunOptions(
                    thread_id='thr-stream',
                    session_id='ses-stream',
                    user_id='usr-sdk',
                    stream_response=True,
                    resume_from_position=_STREAM_RESUME_POSITION,
                ),
            ),
        )
    finally:
        current_reporter.reset(reporter_token)

    assert [event.position for event in events] == [4, 5]
    assert [event.event_type for event in events] == ['status_message_chunk', 'final']
    assert reporter.calls == [('ses-stream', 'orchestrator_agent')]


def test_client_time_travel_stream_posts_checkpoint_then_reads_accepted_session() -> None:
    """A same-thread rewind streams the newly accepted checkpoint continuation."""
    calls: list[tuple[str, str, JsonObject | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, _json_body(request)))
        if request.url.path == '/v1/threads/thr-rewind/time-travel':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-rewind',
                    'session_id': 'ses-rewritten',
                    'stream_position': 0,
                },
            )
        assert request.url.path == '/v1/sessions/ses-rewritten/events'
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 1\nevent: final\n'
                'data: {"event_id":"evt-rewritten","ordinal":"ord-1","type":"final",'
                '"session_id":"ses-rewritten","thread_id":"thr-rewind",'
                '"root_event_id":"evt-rewritten","trigger_chain_depth":0,'
                '"job_depth":0,"job_index":0,"actor":"agent:invoke","mode":"hosted",'
                '"payload":{"message":{"message_id":"msg-final","role":"assistant",'
                '"content":"rewritten","ts":"2026-07-04T12:00:00Z"}},'
                '"ts":"2026-07-04T12:00:00Z"}\n\n'
            ),
        )

    client = _client(handler)
    events = list(
        client.time_travel_thread_stream(
            'thr-rewind',
            checkpoint_id='ses-checkpoint',
            message='replace this prompt',
            options=RunOptions(user_id='usr-sdk', stream_response=True),
            force_final_tool=True,
        ),
    )

    assert [event.event_type for event in events] == ['final']
    assert events[0].data['session_id'] == 'ses-rewritten'
    assert calls[0][0:2] == ('POST', '/v1/threads/thr-rewind/time-travel')
    assert calls[0][2] is not None
    assert calls[0][2]['checkpoint_id'] == 'ses-checkpoint'
    assert calls[0][2]['message']['content'] == 'replace this prompt'
    assert calls[0][2]['force_final_tool'] is True
    assert calls[1][0:2] == ('GET', '/v1/sessions/ses-rewritten/events')


@pytest.mark.parametrize(
    ('supplied', 'expected'),
    [
        (None, 'stored-value'),
        ({'serial_number': 'per-call-value'}, 'per-call-value'),
    ],
)
def test_client_time_travel_stream_uses_one_resolved_private_map(
    supplied: dict[object, object] | None,
    expected: str,
) -> None:
    """The request and local hydration share stored values with caller precedence."""
    request_bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/threads/thr-rewind/time-travel':
            body = _json_body(request)
            assert body is not None
            request_bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-rewind',
                    'session_id': 'ses-private-rewritten',
                    'stream_position': 0,
                },
            )
        assert request.url.path == '/v1/sessions/ses-private-rewritten/events'
        return _invoke_sse_response(
            'ses-private-rewritten',
            'serial {_{serial_number}_}',
        )

    store = InMemoryPrivateDataStore()
    asyncio.run(store.remember('thr-rewind', {'serial_number': 'stored-value'}))
    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=store,
    )

    events = list(
        client.time_travel_thread_stream(
            'thr-rewind',
            checkpoint_id='ses-checkpoint',
            message='replace this prompt',
            private_data=supplied,
        ),
    )

    assert request_bodies[0]['private_data'] == {'serial_number': expected}
    assert events[0].data['payload']['message']['content'] == f'serial {expected}'
    assert asyncio.run(store.resolve('thr-rewind', ())) == {'serial_number': expected}


def test_client_thread_methods_map_to_v2_thread_routes() -> None:
    """Thread continuation and approval helpers use the /v1/threads routes."""
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == '/v1/threads':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-thread',
                    'session_id': 'ses-1',
                    'stream_position': 0,
                },
            )
        if request.url.path == '/v1/threads/thr-thread/messages':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-thread',
                    'session_id': 'ses-2',
                    'stream_position': 0,
                },
            )
        if request.url.path == '/v1/threads/thr-thread/time-travel':
            body = _json_body(request)
            assert body is not None
            assert body['checkpoint_id'] == 'ses-1'
            assert body['branch_from_message_id'] == 'msg-assistant-1'
            assert body['message']['content'] == 'rewrite'
            assert body['run_config']['thread_id'] == 'thr-thread'
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-thread',
                    'session_id': 'ses-3',
                    'stream_position': 0,
                },
            )
        if request.url.path == '/v1/threads/thr-thread/approvals/int-1':
            body = _json_body(request)
            assert body == {'approved': True, 'decided_by': 'usr-sdk', 'reason': 'ok'}
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'thread_id': 'thr-thread',
                    'session_id': 'ses-2',
                    'stream_position': 0,
                },
            )
        return httpx.Response(
            HTTPStatus.OK,
            json={
                'thread_id': 'thr-thread',
                'status': 'completed',
                'history': [],
                'created_at': '2026-07-04T12:00:00Z',
                'updated_at': '2026-07-04T12:00:00Z',
            },
        )

    client = _client(handler)

    start = client.start_thread(
        'start',
        options=RunOptions(thread_id='thr-thread', session_id='ses-1', user_id='usr-sdk'),
    )
    continuation = client.post_thread_message(
        'thr-thread',
        'continue',
        options=RunOptions(session_id='ses-2', user_id='usr-sdk'),
    )
    travelled = client.time_travel_thread(
        'thr-thread',
        checkpoint_id='ses-1',
        message='rewrite',
        branch_from_message_id='msg-assistant-1',
        options=RunOptions(session_id='ses-3', user_id='usr-sdk'),
    )
    approval = client.submit_approval(
        'thr-thread',
        'int-1',
        decision=ApprovalDecision(approved=True, decided_by='usr-sdk', reason='ok'),
    )
    state = client.get_thread('thr-thread')

    assert start.session_id == 'ses-1'
    assert continuation.session_id == 'ses-2'
    assert travelled.session_id == 'ses-3'
    assert approval.thread_id == 'thr-thread'
    assert state.status == 'completed'
    assert seen_paths == [
        '/v1/threads',
        '/v1/threads/thr-thread/messages',
        '/v1/threads/thr-thread/time-travel',
        '/v1/threads/thr-thread/approvals/int-1',
        '/v1/threads/thr-thread',
    ]


@pytest.mark.parametrize('operation', ['post', 'rewind'])
@pytest.mark.parametrize('messages', [[], ['first', 'second']])
def test_single_message_thread_operations_reject_lossy_input(
    operation: str,
    messages: list[str],
) -> None:
    """No message may disappear silently, and an empty sequence gets a useful error."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            202, json={'thread_id': 'thr-1', 'session_id': 'ses-1', 'stream_position': 0}
        )

    client = _client(handler)

    def submit() -> None:
        if operation == 'post':
            client.post_thread_message('thr-1', messages)
        else:
            client.time_travel_thread('thr-1', checkpoint_id='cp-1', message=messages)

    with pytest.raises(ValueError, match='exactly one message'):
        submit()
    assert not requests


def test_client_submits_typed_interrupt_response_without_changing_approval() -> None:
    """Non-approval answers use their own discriminated response endpoint."""
    bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        assert body is not None
        bodies.append(body)
        return httpx.Response(
            HTTPStatus.ACCEPTED,
            json={'thread_id': 'thr-1', 'session_id': 'ses-1', 'stream_position': 0},
        )

    client = _client(handler)

    accepted = client.submit_interrupt_response(
        'thr-1',
        'followup-1',
        answer='US East',
        responded_by='usr-sdk',
    )

    assert accepted.session_id == 'ses-1'
    assert bodies == [
        {
            'answer': {'kind': 'free_text', 'value': 'US East'},
            'responded_by': 'usr-sdk',
            'surface': 'sdk',
        },
    ]


def test_client_accepts_v1_client_timezone_option() -> None:
    """Client stores and sends the v1 timezone constructor option."""
    calls: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            calls.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-timezone', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-timezone', 'done')

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        client_timezone='America/Chicago',
        transport=httpx.MockTransport(handler),
    )

    client.invoke('What time is it?', options=RunOptions(thread_id='thr-timezone'))

    assert client.client_timezone == 'America/Chicago'
    assert calls[0]['run_config']['client_timezone'] == 'America/Chicago'


def test_agent_invoke_omits_reasoning_when_none() -> None:
    """Agent.invoke leaves the optional reasoning field out by default."""
    body = _agent_invoke_payload(reasoning=None)

    assert 'reasoning' not in body


def test_agent_invoke_includes_supplied_reasoning() -> None:
    """Agent.invoke forwards an explicitly requested reasoning effort."""
    body = _agent_invoke_payload(reasoning='high')

    assert body['reasoning'] == 'high'


def test_agent_invoke_serializes_declared_reevaluate_control_with_tools() -> None:
    """Invocation-local tools retain the decorator control needed by the owned loop."""
    request_bodies: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            request_bodies.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-reevaluate-wire', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-reevaluate-wire', 'done')

    agent = Agent(name='reevaluate-wire-agent', client=_client(handler))

    @agent.toolify(name='expose_private_values', description='Expose private values.')
    def expose_private_values() -> dict[str, str]:
        return {'secret_token': 'secret-123'}

    @depends_on_reevaluate(expose_private_values, timing='after', instance_control='all')
    @agent.toolify(name='redaction_report', description='Report after reevaluation.')
    def redaction_report() -> dict[str, str]:
        return {'status': 'ok'}

    assert callable(redaction_report)
    _ = agent.invoke('run the redaction report')

    assert len(request_bodies) == 1
    assert request_bodies[0]['reevaluate_controls'] == [
        {
            'kind': 'reevaluate',
            'dependent_tool_id': 'redaction_report',
            'dependent_tool_name': 'redaction_report',
            'tool_id': 'expose_private_values',
            'tool_name': 'expose_private_values',
            'timing': 'after',
            'instance_control': 'all',
        }
    ]


def _client(
    handler: _SyncHandler | _AsyncHandler,
) -> Client:
    transport = httpx.MockTransport(handler)
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=transport,
    )


def _invoke_sse_response(session_id: str, content: str) -> httpx.Response:
    """Return a minimal final-only SSE response for one invoke session."""
    payload = (
        '{"message":{"message_id":"msg-final","role":"assistant",'
        f'"content":"{content}","ts":"2026-07-04T12:00:00Z"}},'
        '"usage":{"input_tokens":1,"output_tokens":1}}'
    )
    data = (
        '{"event_id":"evt-final","ordinal":"ord-1","type":"final",'
        f'"session_id":"{session_id}","root_event_id":"evt-root",'
        f'"payload":{payload},"ts":"2026-07-04T12:00:00Z"}}'
    )
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: 1\nevent: final\ndata: {data}\n\n',
    )


def _artifact_invoke_sse_response() -> httpx.Response:
    artifact: JsonObject = {
        'artifact_id': 'artifact-sdk',
        'logical_output_id': 'report',
        'revision': 1,
        'kind': 'image',
        'mime_type': 'image/png',
        'created_at': '2026-08-11T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy-sdk',
            'retention_class': 'artifact-30d',
            'expires_at': '2026-09-10T12:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.png',
        'size_bytes': 13,
        'sha256': '0' * 64,
        'producer': {
            'producer_class': 'hosted_tool',
            'producer_id': 'system-create-artifact',
            'root_invocation_id': 'invocation-sdk',
            'session_id': 'ses-artifact-sdk',
            'call_id': 'call-create-artifact',
        },
        'validation_receipt': {
            'receipt_id': 'receipt-sdk',
            'status': 'validated',
            'validator_profile': 'image-validator-v1',
            'validator_version': '1.0.0',
        },
        'safe_preview': {'kind': 'image', 'width_pixels': 4, 'height_pixels': 4},
        'retrieval_action': {'relation': 'artifact.download_authorization'},
    }
    data: JsonObject = {
        'event_id': 'evt-final-artifact',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': 'ses-artifact-sdk',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-final-artifact',
                'role': 'assistant',
                'content': 'The report is ready.',
                'artifact_refs': [artifact],
                'ts': '2026-08-11T12:00:00Z',
            },
            'artifact_refs': [artifact],
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        },
        'ts': '2026-08-11T12:00:00Z',
    }
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: 1\nevent: final\ndata: {json.dumps(data)}\n\n',
    )


def _json_body(request: httpx.Request) -> JsonObject | None:
    if not request.content:
        return None
    return cast('JsonObject', json.loads(request.content.decode('utf-8')))


def _agent_invoke_payload(*, reasoning: Literal['high'] | None) -> JsonObject:
    calls: list[JsonObject] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            body = _json_body(request)
            assert body is not None
            calls.append(body)
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'ses-reasoning', 'stream_position': 0},
            )
        return _invoke_sse_response('ses-reasoning', 'done')

    agent = Agent(name='reasoning-agent', client=_client(handler))
    agent.invoke(
        'hello',
        reasoning=reasoning,
        options=RunOptions(thread_id='thr-reasoning'),
    )

    assert len(calls) == 1
    return calls[0]


def test_tool_specs_advertise_the_configured_execution_timeout() -> None:
    """The wire must not cap every SDK tool at 30s.

    The server uses spec.timeout_ms verbatim as its broker wait deadline, and the SDK
    withholds a layer's outcomes until the slowest tool in it finishes. A hardcoded
    30s therefore expired whole layers server-side on slower demos, terminated the
    run, and made the late result batch fail with 409 invoke_not_running.
    """

    def sample(value: str) -> str:
        return value

    metadata = ToolMetadata(
        name='slow_tool',
        description='Take a while.',
        input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
        target=sample,
    )

    spec = _function_tool_spec(metadata)

    ceiling = 600_000  # maivn_contracts.tools.model.TimeoutMs upper bound
    assert spec['timeout_ms'] == min(int(DEFAULT_TOOL_EXECUTION_TIMEOUT * 1000), ceiling)
    assert spec['timeout_ms'] <= ceiling


def test_tool_spec_wire_publishes_argument_producer_requirements() -> None:
    """Planners can see the caller-authored compose-before-consume DAG edge."""

    @requires_producer_reference(
        'block.composition',
        producer_tool_name='DOCUMENTS_compose_artifact',
    )
    def put_block(block: dict[str, object]) -> dict[str, object]:
        return block

    metadata = ToolMetadata(
        name='DOCUMENTS_put_block',
        description='Upsert a document block.',
        input_schema={'type': 'object', 'properties': {'block': {'type': 'object'}}},
        target=put_block,
    )

    spec = _function_tool_spec(metadata)

    assert spec['argument_producer_requirements'] == [
        {
            'argument_path': 'block.composition',
            'producer_tool_name': 'DOCUMENTS_compose_artifact',
            'reference_kind': 'composition_reference',
        }
    ]
    assert 'tool_dependencies' not in spec


def test_compose_argument_policy_publishes_target_policy_and_required_producer() -> None:
    """The dedicated decorator carries target policy and the synthesis DAG edge."""

    @compose_argument_policy('query', mode='require', approval='explicit')
    def validate_query(query: str) -> str:
        return query

    metadata = ToolMetadata(
        name='validate_query',
        description='Validate SQL.',
        input_schema={
            'type': 'object',
            'properties': {'query': {'type': 'string', 'description': 'SQL query text.'}},
            'required': ['query'],
            'additionalProperties': False,
        },
        target=validate_query,
    )

    spec = _function_tool_spec(metadata)

    query_schema = cast(
        'dict[str, object]', cast('dict[str, object]', spec['input_schema'])['properties']
    )['query']
    assert cast('dict[str, object]', query_schema)['x-maivn-compose-argument-policy'] == {
        'mode': 'require',
        'approval': 'explicit',
    }
    assert spec['argument_producer_requirements'] == [
        {
            'argument_path': 'query',
            'producer_tool_name': 'compose_argument',
            'reference_kind': 'composition_reference',
        }
    ]


def test_tool_spec_wire_preserves_explicit_dependency_revision_policy() -> None:
    """Only the developer's opt-in policy crosses from SDK metadata to the contract."""

    def source() -> dict[str, int]:
        return {'value': 1}

    @depends_on_tool(source, 'source', revision_policy='on_validation_error')
    def consumer(source: object) -> object:
        return source

    metadata = ToolMetadata(
        name='consumer',
        description='Consume a source.',
        input_schema={'type': 'object', 'properties': {'source': {}}},
        target=consumer,
    )

    spec = _function_tool_spec(metadata)

    assert spec['tool_dependencies'] == [
        {
            'tool_name': 'source',
            'arg_name': 'source',
            'revision_policy': 'on_validation_error',
        }
    ]


def test_tool_spec_wire_preserves_conversation_dependency_scope() -> None:
    """Stable values can opt in without making confirmation dependencies reusable."""

    @depends_on_tool('collect_name', 'name', result_scope='conversation')
    @depends_on_tool('confirm', 'confirmation')
    def summarize(name: object, confirmation: object) -> tuple[object, object]:
        return name, confirmation

    spec = _function_tool_spec(ToolMetadata(name='summarize', target=summarize))
    assert spec['tool_dependencies'] == [
        {'tool_name': 'confirm', 'arg_name': 'confirmation'},
        {'tool_name': 'collect_name', 'arg_name': 'name', 'result_scope': 'conversation'},
    ]
