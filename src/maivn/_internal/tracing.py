"""Optional SDK spans and W3C propagation without a mandatory OTel import."""
# ruff: noqa: PLC0415 -- OTel is optional and imports belong only on enabled paths.

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Generator, Mapping


_ENDPOINT = 'OTEL_EXPORTER_OTLP_ENDPOINT'
_SPAN_KEY = 'maivn.sdk.http.span'
_HTTP_ERROR_MIN_STATUS = 400


class SdkTracing:
    """Small OpenTelemetry API adapter used only when explicitly enabled."""

    def __init__(self, *, tracer: Any, propagator: Any) -> None:
        self._tracer = tracer
        self._propagator = propagator

    @classmethod
    def from_environment(cls) -> SdkTracing | None:
        """Resolve the application provider only when the standard endpoint is set."""
        if not os.environ.get(_ENDPOINT, '').strip():
            return None
        try:
            from opentelemetry import propagate, trace
        except ModuleNotFoundError:
            return None
        return cls(
            tracer=trace.get_tracer('maivn.sdk'),
            propagator=propagate.get_global_textmap(),
        )

    async def on_request(self, request: Any) -> None:
        """Open one transport span and inject its trace context."""
        from opentelemetry.trace import SpanKind, set_span_in_context

        span = self._tracer.start_span(
            f'{request.method.upper()} {request.url.host}',
            kind=SpanKind.CLIENT,
            attributes={
                'http.request.method': request.method.upper(),
                'url.full': str(
                    request.url.copy_with(username='', password='', query=None, fragment=None)
                ),
                'url.path': request.url.path,
                'url.scheme': request.url.scheme,
                'server.address': request.url.host,
            },
        )
        request.extensions[_SPAN_KEY] = span
        self._propagator.inject(
            carrier=request.headers,
            context=set_span_in_context(span),
        )

    async def on_response(self, response: Any) -> None:
        """Record headers and keep the transport span open until its body closes."""
        span = response.request.extensions.get(_SPAN_KEY)
        if span is None:
            return
        span.set_attribute('http.response.status_code', response.status_code)
        if response.status_code >= _HTTP_ERROR_MIN_STATUS:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, f'HTTP {response.status_code}'))
        if response.is_closed:
            self.finish(response.request)
        else:
            response.stream = _TracedResponseStream(response.stream, response.request, self)

    def finish(self, request: Any) -> None:
        """End a completed or explicitly closed response exactly once."""
        span = request.extensions.pop(_SPAN_KEY, None)
        if span is not None:
            span.end()

    def on_error(self, request: Any, error: BaseException) -> None:
        """Close a failed request even when no HTTP response exists."""
        span = request.extensions.pop(_SPAN_KEY, None)
        if span is not None:
            _record_error(span, error)
            span.end()

    @property
    def event_hooks(self) -> dict[str, list[Any]]:
        return {'request': [self.on_request], 'response': [self.on_response]}

    @contextmanager
    def operation(
        self,
        operation: str,
        *,
        attributes: Mapping[str, str | int | bool | None] | None = None,
    ) -> Generator[Any]:
        """Trace one SDK lifecycle operation without recording customer content."""
        safe: dict[str, str | int | bool] = {'maivn.sdk.operation': operation}
        safe.update({key: value for key, value in (attributes or {}).items() if value is not None})
        with self._tracer.start_as_current_span(
            f'maivn.sdk.{operation}',
            attributes=safe,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                yield span
            except BaseException as error:
                _record_error(span, error)
                raise


class _TracedResponseStream(httpx.AsyncByteStream):
    """Observe body failures without buffering or changing streamed chunks."""

    def __init__(
        self, stream: httpx.AsyncByteStream, request: httpx.Request, tracing: SdkTracing
    ) -> None:
        self._stream = stream
        self._request = request
        self._tracing = tracing

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._stream:
                yield chunk
        except BaseException as error:
            self._tracing.on_error(self._request, error)
            raise

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        except BaseException as error:
            self._tracing.on_error(self._request, error)
            raise
        finally:
            self._tracing.finish(self._request)


def _record_error(span: Any, error: BaseException) -> None:
    from opentelemetry.trace import Status, StatusCode

    span.set_status(Status(StatusCode.ERROR, type(error).__name__))
    span.add_event(
        'exception',
        attributes={
            'exception.type': type(error).__name__,
            'exception.message': 'SDK operation failed',
            'exception.escaped': True,
        },
    )


@contextmanager
def sdk_operation(
    operation: str,
    *,
    attributes: Mapping[str, str | int | bool | None] | None = None,
) -> Generator[Any | None]:
    """Create an endpoint-driven operation span, or a true no-op."""
    tracing = SdkTracing.from_environment()
    if tracing is None:
        yield None
        return
    with tracing.operation(operation, attributes=attributes) as span:
        yield span


__all__ = ['SdkTracing', 'sdk_operation']
