"""Optional SDK HTTP tracing and W3C propagation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from maivn._internal.tracing import SdkTracing
from maivn._internal.transport.http import HttpJsonClient, close_stream_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def test_http_client_injects_traceparent_and_records_metadata_only() -> None:
    """HTTP context crosses the wire without query, credentials, or bodies."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['traceparent'] = request.headers['traceparent']
        return httpx.Response(200, json={'ok': True})

    tracing = SdkTracing(
        tracer=provider.get_tracer('test.sdk.http'),
        propagator=TraceContextTextMapPropagator(),
    )
    client = HttpJsonClient(
        base_url='https://platform.example',
        api_key='private-key',
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
        tracing=tracing,
    )
    assert asyncio.run(client.post('/v1/invoke?secret=value', {'prompt': 'private prompt'})) == {
        'ok': True
    }
    provider.force_flush()

    assert seen['traceparent'].startswith('00-')
    span = exporter.get_finished_spans()[0]
    assert span.name == 'POST platform.example'
    assert dict(span.attributes or {}) == {
        'http.request.method': 'POST',
        'url.full': 'https://platform.example/v1/invoke',
        'url.path': '/v1/invoke',
        'url.scheme': 'https',
        'server.address': 'platform.example',
        'http.response.status_code': 200,
    }
    assert 'private-key' not in repr(span)
    assert 'private prompt' not in repr(span)
    assert 'secret=value' not in repr(span)
    provider.shutdown()


def test_transport_failure_ends_span_without_recording_private_error_text() -> None:
    """A failed connection remains observable without exporting its message."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def handler(request: httpx.Request) -> httpx.Response:
        message = 'private-key private@example.com'
        raise httpx.ConnectError(message, request=request)

    try:
        tracing = SdkTracing(
            tracer=provider.get_tracer('test.sdk.http'),
            propagator=TraceContextTextMapPropagator(),
        )
        client = HttpJsonClient(
            base_url='https://platform.example',
            api_key='private-key',
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
            tracing=tracing,
        )
        with pytest.raises(httpx.ConnectError):
            asyncio.run(client.get('/v1/runs'))
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code.name == 'ERROR'
        error_attributes = spans[0].events[0].attributes
        assert error_attributes is not None
        assert error_attributes['exception.message'] == 'SDK operation failed'
        assert 'private-key' not in str(spans[0].events[0].attributes)
        assert 'private@example.com' not in str(spans[0].events[0].attributes)
    finally:
        provider.shutdown()


@pytest.mark.parametrize('streaming', [False, True])
def test_body_failure_is_recorded_after_successful_headers(*, streaming: bool) -> None:
    """Both buffered and streamed bodies can fail after HTTP 200 headers."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: first token\n\n'
            message = 'private-key private@example.com'
            raise httpx.ReadError(message)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BrokenBody())

    async def exercise() -> None:
        client = HttpJsonClient(
            base_url='https://platform.example',
            api_key='private-key',
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
            tracing=SdkTracing(
                tracer=provider.get_tracer('test.sdk.http'),
                propagator=TraceContextTextMapPropagator(),
            ),
        )
        if not streaming:
            with pytest.raises(httpx.ReadError):
                await client.get('/v1/runs')
            return
        response = await client.stream('/v1/events')
        try:
            assert not exporter.get_finished_spans(), 'headers must not finish a live stream'
            with pytest.raises(httpx.ReadError):
                await response.aread()
        finally:
            await close_stream_response(response)

    try:
        asyncio.run(exercise())
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code.name == 'ERROR'
        assert dict(span.attributes or {})['http.response.status_code'] == httpx.codes.OK
        assert len(span.events) == 1
        assert dict(span.events[0].attributes or {})['exception.type'] == 'ReadError'
        assert 'private-key' not in str(span.events)
        assert 'private@example.com' not in str(span.events)
    finally:
        provider.shutdown()
