"""Characterize optional SDK operation spans and streamed-response span lifecycles."""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from maivn import MaivnHTTPError
from maivn._internal.tracing import SdkTracing, sdk_operation
from maivn._internal.transport.http import HttpJsonClient, close_stream_response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator, Iterator

_ENDPOINT = 'OTEL_EXPORTER_OTLP_ENDPOINT'


@pytest.fixture
def recorded() -> Iterator[tuple[TracerProvider, InMemorySpanExporter]]:
    """Provide an in-memory tracer provider that is always shut down."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider, exporter
    provider.shutdown()


def _tracing(provider: TracerProvider) -> SdkTracing:
    return SdkTracing(
        tracer=provider.get_tracer('test.sdk.ops'),
        propagator=TraceContextTextMapPropagator(),
    )


def _client(handler: httpx.MockTransport, tracing: SdkTracing) -> HttpJsonClient:
    return HttpJsonClient(
        base_url='https://platform.example',
        api_key='private-key',
        timeout_seconds=1,
        transport=handler,
        tracing=tracing,
    )


@pytest.mark.parametrize('endpoint', [None, '', '   '])
def test_sdk_operation_is_a_no_op_without_an_endpoint(
    monkeypatch: pytest.MonkeyPatch, endpoint: str | None
) -> None:
    """A missing or blank exporter endpoint yields no span at all."""
    if endpoint is None:
        monkeypatch.delenv(_ENDPOINT, raising=False)
    else:
        monkeypatch.setenv(_ENDPOINT, endpoint)
    assert SdkTracing.from_environment() is None
    with sdk_operation('claim', attributes={'a': 1}) as span:
        assert span is None


def test_sdk_operation_is_a_no_op_when_opentelemetry_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint without the optional OpenTelemetry package still yields no span."""
    monkeypatch.setenv(_ENDPOINT, 'http://collector.invalid:4318')
    monkeypatch.setitem(sys.modules, 'opentelemetry', None)
    assert SdkTracing.from_environment() is None
    with sdk_operation('claim') as span:
        assert span is None


def test_sdk_operation_with_an_endpoint_yields_a_span_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an endpoint and OpenTelemetry the operation yields a span and keeps errors."""
    monkeypatch.setenv(_ENDPOINT, 'http://collector.invalid:4318')
    assert isinstance(SdkTracing.from_environment(), SdkTracing)
    with sdk_operation('serve') as span:
        assert span is not None

    def fail() -> None:
        with sdk_operation('serve'):
            message = 'kept'
            raise LookupError(message)

    with pytest.raises(LookupError, match='kept'):
        fail()


def test_operation_span_records_safe_attributes_only(
    recorded: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """The span carries the operation name and non-None attributes; status stays unset."""
    provider, exporter = recorded
    with _tracing(provider).operation(
        'claim', attributes={'maivn.serve.cycle': 'claim', 'count': 2, 'skip': None}
    ):
        pass
    (span,) = exporter.get_finished_spans()
    assert span.name == 'maivn.sdk.claim'
    assert dict(span.attributes or {}) == {
        'maivn.sdk.operation': 'claim',
        'maivn.serve.cycle': 'claim',
        'count': 2,
    }
    assert span.status.status_code.name == 'UNSET'
    assert span.events == ()


class _RecordingTracer:
    """Tracer double that records exactly what an operation asks it to open."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, dict[str, object]]] = []

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: object) -> Generator[str, None, None]:
        self.opened.append((name, kwargs))
        yield 'span'


def test_operation_passes_only_non_none_attributes_and_disables_auto_recording() -> None:
    """None-valued attributes never reach the tracer; exceptions are recorded manually."""
    tracer = _RecordingTracer()
    tracing = SdkTracing(tracer=tracer, propagator=TraceContextTextMapPropagator())
    with tracing.operation('serve', attributes={'kept': 'yes', 'dropped': None}) as span:
        assert span == 'span'
    assert tracer.opened == [
        (
            'maivn.sdk.serve',
            {
                'attributes': {'maivn.sdk.operation': 'serve', 'kept': 'yes'},
                'record_exception': False,
                'set_status_on_exception': False,
            },
        )
    ]


def test_operation_span_records_a_value_free_error_and_reraises(
    recorded: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """Failures mark the span ERROR with the type name only and propagate unchanged."""
    provider, exporter = recorded

    def fail() -> None:
        with _tracing(provider).operation('claim'):
            message = 'private detail'
            raise ValueError(message)

    with pytest.raises(ValueError, match='private detail'):
        fail()
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == 'ERROR'
    assert span.status.description == 'ValueError'
    (event,) = span.events
    assert event.name == 'exception'
    assert dict(event.attributes or {}) == {
        'exception.type': 'ValueError',
        'exception.message': 'SDK operation failed',
        'exception.escaped': True,
    }
    assert 'private detail' not in repr(span.to_json())


def test_http_error_status_marks_the_transport_span(
    recorded: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """A 4xx/5xx response marks the closed span ERROR with the status code."""
    provider, exporter = recorded

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={'detail': 'Not found', 'code': 'not_found'})

    client = _client(httpx.MockTransport(handler), _tracing(provider))
    with pytest.raises(MaivnHTTPError):
        asyncio.run(client.get('/v1/runs'))
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == 'ERROR'
    assert span.status.description == 'HTTP 404'
    assert dict(span.attributes or {})['http.response.status_code'] == httpx.codes.NOT_FOUND


def test_response_without_a_span_is_ignored(
    recorded: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """A response whose request never opened a span records nothing."""
    provider, exporter = recorded
    response = httpx.Response(500, request=httpx.Request('GET', 'https://platform.example/x'))
    asyncio.run(_tracing(provider).on_response(response))
    _tracing(provider).finish(response.request)
    assert exporter.get_finished_spans() == ()


def test_streamed_body_read_to_completion_finishes_the_span_once(
    recorded: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """A fully consumed stream ends its span exactly once, without an error."""
    provider, exporter = recorded

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: one\n\n'
            yield b'data: two\n\n'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Body())

    async def exercise() -> bytes:
        response = await _client(httpx.MockTransport(handler), _tracing(provider)).stream('/v1/e')
        try:
            body = await response.aread()
        finally:
            await close_stream_response(response)
        return body

    assert asyncio.run(exercise()) == b'data: one\n\ndata: two\n\n'
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == 'UNSET'
    assert span.events == ()


def test_stream_close_failure_is_recorded_and_still_finishes(
    recorded: tuple[TracerProvider, InMemorySpanExporter],
) -> None:
    """A failing body close records a value-free error, ends the span and re-raises."""
    provider, exporter = recorded

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: one\n\n'

        async def aclose(self) -> None:
            message = 'private close detail'
            raise httpx.CloseError(message)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Body())

    async def exercise() -> None:
        response = await _client(httpx.MockTransport(handler), _tracing(provider)).stream('/v1/e')
        with pytest.raises(httpx.CloseError, match='private close detail'):
            await response.aclose()

    asyncio.run(exercise())
    (span,) = exporter.get_finished_spans()
    assert span.status.status_code.name == 'ERROR'
    assert span.status.description == 'CloseError'
    assert dict(span.events[0].attributes or {})['exception.type'] == 'CloseError'
    assert 'private close detail' not in str(span.events)
