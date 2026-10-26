"""Characterize SDK transport: auth headers, redirects, error envelopes, leases and SSE."""

from __future__ import annotations

import asyncio
import io
import json
from typing import TYPE_CHECKING

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from maivn._internal.errors import MaivnHTTPError
from maivn._internal.plan_limits import PlanLimitExceededError
from maivn._internal.tracing import SdkTracing
from maivn._internal.transport.http import HttpJsonClient, close_stream_response
from maivn._internal.transport.leases import LoopLocalTransport, lease_transport
from maivn._internal.transport.sse_client import SseParseError, iter_sse_events, parse_sse_frame

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from typing_extensions import Self

_KEY = 'sk-transport-sentinel-key'
_BASE = 'https://api.example'


def _empty_object(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


class Recorder:
    """Answer every request with one configured response and record what was sent."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        """Default to an empty JSON object reply."""
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self._respond = respond or _empty_object

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """Record the request and its full body."""
        self.requests.append(request)
        self.bodies.append(await request.aread())
        return self._respond(request)

    def client(self) -> HttpJsonClient:
        """Return a JSON client wired to this recorder."""
        return HttpJsonClient(
            base_url=_BASE,
            api_key=_KEY,
            timeout_seconds=1,
            transport=httpx.MockTransport(self.handle),
        )


def _error(response: httpx.Response) -> MaivnHTTPError:
    recorder = Recorder(lambda _request: response)
    with pytest.raises(MaivnHTTPError) as caught:
        asyncio.run(recorder.client().post('/v1/x', {}))
    return caught.value


def test_every_verb_sends_the_bearer_key_to_the_configured_origin() -> None:
    """Each JSON verb authenticates with the bearer key and JSON content type."""
    recorder = Recorder()
    client = recorder.client()

    async def exercise() -> None:
        await client.post('/v1/post', {'a': 1})
        await client.patch('/v1/patch', {'b': 2})
        await client.delete('/v1/delete')
        await client.get('/v1/get', params={'page': 2}, headers={'X-Extra': 'yes'})
        await client.post_bytes('/v1/bytes', {'c': 3})
        await client.get_bytes('/v1/raw', params={'id': 'a'})

    asyncio.run(exercise())
    assert [(request.method, str(request.url)) for request in recorder.requests] == [
        ('POST', 'https://api.example/v1/post'),
        ('PATCH', 'https://api.example/v1/patch'),
        ('DELETE', 'https://api.example/v1/delete'),
        ('GET', 'https://api.example/v1/get?page=2'),
        ('POST', 'https://api.example/v1/bytes'),
        ('GET', 'https://api.example/v1/raw?id=a'),
    ]
    assert all(
        request.headers['authorization'] == f'Bearer {_KEY}' for request in recorder.requests
    )
    assert all(
        request.headers['content-type'] == 'application/json' for request in recorder.requests
    )
    assert recorder.requests[3].headers['x-extra'] == 'yes'
    assert recorder.requests[5].headers['accept'] == 'application/octet-stream'
    assert [json.loads(body) for body in recorder.bodies if body] == [{'a': 1}, {'b': 2}, {'c': 3}]


def test_redirects_are_returned_not_followed() -> None:
    """A redirect never carries the bearer key to another origin."""
    recorder = Recorder(
        lambda _request: httpx.Response(307, headers={'location': 'https://evil.example/steal'})
    )
    assert asyncio.run(recorder.client().post('/v1/x', {})) == {}
    assert [request.url.host for request in recorder.requests] == ['api.example']


def test_traced_clients_do_not_follow_redirects_either() -> None:
    """The tracing client keeps the same no-redirect rule as the plain client."""
    recorder = Recorder(
        lambda _request: httpx.Response(307, headers={'location': 'https://evil.example/steal'})
    )
    tracing = SdkTracing(
        tracer=TracerProvider().get_tracer('test.security.transport'),
        propagator=TraceContextTextMapPropagator(),
    )
    client = HttpJsonClient(
        base_url=_BASE,
        api_key=_KEY,
        timeout_seconds=1,
        transport=httpx.MockTransport(recorder.handle),
        tracing=tracing,
    )
    assert asyncio.run(client.post('/v1/x', {})) == {}
    assert [request.url.host for request in recorder.requests] == ['api.example']


def test_binary_responses_keep_content_and_headers_only() -> None:
    """Binary replies expose bytes and headers, never the authenticated request."""
    recorder = Recorder(
        lambda _request: httpx.Response(200, content=b'\x00raw', headers={'x-a': '1'})
    )
    result = asyncio.run(recorder.client().get_bytes('/v1/raw'))
    assert result.content == b'\x00raw'
    assert result.headers['x-a'] == '1'
    assert _KEY not in repr(result)


def test_framed_file_body_is_prefix_control_bytes_then_source() -> None:
    """The framed upload is a 4-byte length, canonical control JSON, the file, then source."""
    recorder = Recorder(lambda _request: httpx.Response(200, json={'ok': True}))
    control = {'z': 'é', 'a': [1, 2]}
    result = asyncio.run(
        recorder.client().post_framed_file(
            '/v1/files',
            control=control,
            stream=io.BytesIO(b'file-bytes'),
            size_bytes=10,
            source=b'src',
        )
    )
    assert result == {'ok': True}
    canonical = b'{"a":[1,2],"z":"\\u00e9"}'
    assert recorder.bodies == [
        len(canonical).to_bytes(4, 'big') + canonical + b'file-bytes' + b'src'
    ]
    headers = recorder.requests[0].headers
    assert headers['content-length'] == str(4 + len(canonical) + 10 + 3)
    assert headers['content-type'] == 'application/vnd.maivn.tool-file+binary'
    assert headers['accept'] == 'application/json'
    assert headers['authorization'] == f'Bearer {_KEY}'


def test_framed_file_without_source_sends_only_the_file() -> None:
    """Omitting the source sends no trailing bytes and counts none in the length."""
    recorder = Recorder()
    asyncio.run(
        recorder.client().post_framed_file(
            '/v1/files', control={}, stream=io.BytesIO(b'abc'), size_bytes=3
        )
    )
    assert recorder.bodies == [b'\x00\x00\x00\x02{}abc']
    assert recorder.requests[0].headers['content-length'] == '9'


def test_stream_requests_event_streams_and_closes_its_client() -> None:
    """Streaming GETs ask for SSE, authenticate, and close their own client."""
    recorder = Recorder(lambda _request: httpx.Response(200, content=b'id: 1\n\n'))
    client = recorder.client()

    async def exercise() -> bytes:
        response = await client.stream('/v1/events', params={'after': 3}, headers={'Accept': 'x'})
        body = await response.aread()
        await close_stream_response(response)
        return body

    assert asyncio.run(exercise()) == b'id: 1\n\n'
    request = recorder.requests[0]
    assert request.headers['accept'] == 'text/event-stream'
    assert request.headers['authorization'] == f'Bearer {_KEY}'
    assert str(request.url) == 'https://api.example/v1/events?after=3'


def test_close_stream_response_tolerates_a_response_without_a_client() -> None:
    """Closing a bare response does not require an owning client."""
    response = httpx.Response(200, content=b'x')
    asyncio.run(close_stream_response(response))
    assert response.is_closed


def test_bounded_context_cannot_be_reentered() -> None:
    """A second concurrent context on one client is refused; exit without entry is safe."""
    client = Recorder().client()

    async def exercise() -> None:
        await client.__aexit__(None, None, None)
        async with client:
            with pytest.raises(RuntimeError, match=r'^HTTP client context is already open$'):
                await client.__aenter__()
        async with client:
            assert await client.post('/v1/again', {}) == {}

    asyncio.run(exercise())


def test_empty_success_body_is_an_empty_object_and_lists_are_refused() -> None:
    """A JSON client accepts only objects or empty bodies from success replies."""
    assert asyncio.run(Recorder(lambda _r: httpx.Response(204)).client().post('/v1/x', {})) == {}
    error = _error(httpx.Response(200, json=[1, 2]))
    assert type(error) is MaivnHTTPError
    assert (error.status_code, error.reason) == (
        200,
        'expected JSON object response from mAIvn API',
    )


def test_error_envelope_maps_every_member() -> None:
    """Detail, code, context, valid fields and integer retry_after reach the error."""
    error = _error(
        httpx.Response(
            422,
            json={
                'detail': 'Invalid request',
                'code': 'validation_failed',
                'context': {'limit': 5},
                'retry_after': 7,
                'fields': [
                    {'field': 'name', 'message': 'required'},
                    {'field': 'age'},
                    {'field': 3, 'message': 'bad'},
                    'not-a-field',
                ],
            },
        )
    )
    assert (error.status_code, error.reason, error.code, error.detail, error.retry_after) == (
        422,
        'Invalid request',
        'validation_failed',
        {'limit': 5},
        7,
    )
    assert error.fields == [{'field': 'name', 'message': 'required'}]
    assert str(error) == 'mAIvn API returned 422: Invalid request - name: required'


def test_malformed_envelope_members_are_dropped() -> None:
    """Non-dict context, non-int retry_after, non-list fields and blank detail are ignored."""
    error = _error(
        httpx.Response(
            503,
            json={'detail': '', 'code': '', 'context': ['x'], 'retry_after': '7', 'fields': {}},
        )
    )
    assert (error.reason, error.code, error.detail, error.retry_after, error.fields) == (
        'Service Unavailable',
        None,
        None,
        None,
        [],
    )


@pytest.mark.parametrize('content', [b'not json', b'[1]'])
def test_non_envelope_errors_use_the_status_phrase(content: bytes) -> None:
    """An error body that is not a JSON object yields the status phrase only."""
    error = _error(httpx.Response(404, content=content))
    assert (error.reason, error.code, error.fields) == ('Not Found', None, [])


def test_plan_limit_code_is_typed() -> None:
    """The plan-limit code produces its dedicated subclass with the status unchanged."""
    error = _error(
        httpx.Response(402, json={'detail': 'Out of runs', 'code': 'plan_limit_exceeded'})
    )
    assert type(error) is PlanLimitExceededError
    assert error.status_code == 402  # noqa: PLR2004 - status is carried through.


def test_errors_never_echo_the_bearer_key() -> None:
    """An error raised for an authenticated request does not contain the key."""
    error = _error(httpx.Response(401, json={'detail': 'Unauthorized'}))
    assert _KEY not in str(error)
    assert _KEY not in repr(error)


def test_absent_transport_is_not_leased() -> None:
    """Default transports are owned by their own clients and never wrapped."""
    assert lease_transport(None) is None


def test_closed_lease_refuses_requests_and_closes_once() -> None:
    """A closed lease cannot be reused, and closing it again is a no-op."""
    recorder = Recorder()
    lease = lease_transport(httpx.MockTransport(recorder.handle))
    assert lease is not None

    async def exercise() -> None:
        request = httpx.Request('GET', 'https://api.example/x')
        await lease.handle_async_request(request)
        await lease.aclose()
        await lease.aclose()
        with pytest.raises(RuntimeError, match=r'^HTTP transport lease is closed$'):
            await lease.handle_async_request(request)

    asyncio.run(exercise())
    assert len(recorder.requests) == 1


class _FailingEntryTransport(httpx.AsyncBaseTransport):
    """A transport whose context entry fails once."""

    def __init__(self) -> None:
        self.fail_entry = True
        self.closes = 0

    async def __aenter__(self) -> Self:
        if self.fail_entry:
            self.fail_entry = False
            message = 'entry failed'
            raise OSError(message)
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'path': request.url.path})

    async def aclose(self) -> None:
        self.closes += 1


def test_failed_transport_entry_releases_the_pool() -> None:
    """A failed entry releases its claim so the next request can use the pool."""
    transport = _FailingEntryTransport()
    client = HttpJsonClient(base_url=_BASE, api_key=_KEY, timeout_seconds=1, transport=transport)
    with pytest.raises(OSError, match='entry failed'):
        asyncio.run(client.post('/v1/first', {}))
    assert asyncio.run(client.post('/v1/second', {})) == {'path': '/v1/second'}


class _FailingCloseTransport(httpx.AsyncBaseTransport):
    """A transport whose cleanup fails."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'path': request.url.path})

    async def aclose(self) -> None:
        message = 'close failed'
        raise OSError(message)


def test_failed_pool_cleanup_disables_the_pool() -> None:
    """After cleanup fails, the injected pool refuses every later lease."""
    transport = _FailingCloseTransport()
    client = HttpJsonClient(base_url=_BASE, api_key=_KEY, timeout_seconds=1, transport=transport)
    with pytest.raises(OSError, match='close failed'):
        asyncio.run(client.post('/v1/first', {}))
    with pytest.raises(RuntimeError, match=r'^Injected HTTP transport cleanup failed$'):
        asyncio.run(client.post('/v1/second', {}))


class _CountingPool(httpx.AsyncBaseTransport):
    """A pool that counts requests and closes."""

    def __init__(self) -> None:
        self.requests = 0
        self.closes = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(200, json={'path': request.url.path})

    async def aclose(self) -> None:
        self.closes += 1


def test_loop_local_transport_keeps_one_pool_per_loop() -> None:
    """Each event loop gets its own pool; outside a loop every call gets a new one."""
    created: list[_CountingPool] = []

    def factory() -> httpx.AsyncBaseTransport:
        pool = _CountingPool()
        created.append(pool)
        return pool

    local = LoopLocalTransport(factory)
    assert local.for_current_loop() is not local.for_current_loop()

    async def exercise() -> None:
        assert local.for_current_loop() is local.for_current_loop()
        await local.handle_async_request(httpx.Request('GET', 'https://api.example/a'))
        await local.aclose()

    asyncio.run(exercise())
    asyncio.run(exercise())
    assert [(pool.requests, pool.closes) for pool in created] == [(0, 0), (0, 0), (1, 1), (1, 1)]


def test_loop_local_transport_is_leased_per_loop() -> None:
    """Leasing a loop-local transport leases the current loop's own pool."""
    recorder = Recorder()
    local = LoopLocalTransport(lambda: httpx.MockTransport(recorder.handle))
    client = HttpJsonClient(base_url=_BASE, api_key=_KEY, timeout_seconds=1, transport=local)
    assert asyncio.run(client.post('/v1/a', {})) == {}
    assert asyncio.run(client.post('/v1/b', {})) == {}
    assert [request.url.path for request in recorder.requests] == ['/v1/a', '/v1/b']


def test_sse_frame_parses_all_line_endings_and_defaults() -> None:
    """CRLF and CR endings parse; a blank event name falls back to message."""
    event = parse_sse_frame('id: 4\r\nevent:   \rdata: {"k":\r\ndata: 1}\nretry: 9\n\n')
    assert (event.position, event.event_type, event.data) == (4, 'message', {'k': 1})


def test_sse_frame_bytes_replace_invalid_utf8() -> None:
    """Undecodable bytes are replaced rather than raising."""
    event = parse_sse_frame(b'id: 1\nevent: n\xffx\ndata: {"t": "\xff"}\n\n')
    assert event.event_type == 'n�x'
    assert event.data == {'t': '�'}


def test_sse_frame_without_data_has_an_empty_object() -> None:
    """A frame with an id and no data yields an empty payload."""
    assert parse_sse_frame('id: 0\n\n').data == {}


@pytest.mark.parametrize(
    ('frame', 'message'),
    [
        ('event: x\ndata: {}\n\n', 'SSE frame is missing an id field'),
        ('id: -1\n\n', 'SSE id field must be a non-negative integer'),
        ('id: 1.5\n\n', 'SSE id field must be a non-negative integer'),
        ('id:\n\n', 'SSE id field must be a non-negative integer'),
        ('id: 1\ndata: [1]\n\n', 'SSE data field must contain a JSON object'),
    ],
)
def test_sse_frame_refusals_have_fixed_messages(frame: str, message: str) -> None:
    """Malformed frames fail with fixed messages that never quote the payload."""
    with pytest.raises(SseParseError) as caught:
        parse_sse_frame(frame)
    assert str(caught.value) == message
    assert caught.value.__cause__ is None


def test_sse_invalid_json_message_omits_the_payload() -> None:
    """Invalid JSON is reported with a fixed message that never quotes the payload."""
    with pytest.raises(SseParseError) as caught:
        parse_sse_frame('id: 1\ndata: {"secret": sentinel-value\n\n')
    assert str(caught.value) == 'SSE data field must contain a JSON object'
    assert 'sentinel-value' not in str(caught.value)


def test_sse_stream_skips_comments_and_flushes_a_trailing_frame() -> None:
    """Comments and repeated blank lines are skipped; an unterminated last frame is kept."""

    async def lines() -> AsyncIterator[str]:
        for line in [':keepalive', '', 'id: 1', 'data: {"a": 1}', '', '', 'id: 2', ': note']:
            yield line

    async def collect() -> list[tuple[int, dict[str, object]]]:
        return [(event.position, event.data) async for event in iter_sse_events(lines())]

    assert asyncio.run(collect()) == [(1, {'a': 1}), (2, {})]
