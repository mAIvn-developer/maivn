"""HTTP primitives for the mAIvn SDK client.

Adapted from the v1 SDK client HTTP mixin. v2 uses bearer authentication across
the public `/v1` route surface.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, cast

import httpx

from maivn._internal.errors import MaivnHTTPError
from maivn._internal.plan_limits import PLAN_LIMIT_ERROR_CODE, PlanLimitExceededError
from maivn._internal.tracing import SdkTracing
from maivn._internal.transport.leases import lease_transport

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from types import TracebackType
    from typing import BinaryIO

    from typing_extensions import Self

JsonObject: TypeAlias = dict[str, Any]
JsonValue: TypeAlias = JsonObject | list[Any] | str | int | float | bool | None
HTTP_ERROR_MIN_STATUS = 400


class AsyncTransport(Protocol):
    """Minimal async transport protocol accepted by httpx.AsyncClient."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle one async request."""
        ...


class _TracingAsyncClient(httpx.AsyncClient):
    """Close failed hook spans while preserving httpx transport/proxy selection."""

    def __init__(self, *, tracing: SdkTracing, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tracing = tracing

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        try:
            return await super().send(
                request,
                stream=stream,
                auth=auth,
                follow_redirects=follow_redirects,
            )
        except BaseException as error:
            self._tracing.on_error(request, error)
            raise


class HttpJsonClient:
    """Small async JSON HTTP client with SDK auth headers."""

    @property
    def base_url(self) -> str:
        """Return the configured service origin."""
        return self._base_url

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        tracing: SdkTracing | None = None,
    ) -> None:
        """Create an async JSON client wrapper."""
        self._base_url = base_url
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._tracing = tracing if tracing is not None else SdkTracing.from_environment()
        self._shared_client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        """Keep one connection pool open for a bounded multi-request operation."""
        if self._shared_client is not None:
            message = 'HTTP client context is already open'
            raise RuntimeError(message)
        self._shared_client = self._client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the bounded connection pool."""
        _ = exc_type, exc, traceback
        client = self._shared_client
        self._shared_client = None
        if client is not None:
            await client.aclose()

    async def post(self, path: str, payload: JsonObject) -> JsonObject:
        """POST a JSON object and return a JSON object."""
        async with self._request_client() as client:
            response = await client.post(path, json=payload, headers=self._headers())
            return _response_object(response)

    async def post_bytes(self, path: str, payload: JsonObject) -> HttpBinaryResponse:
        """POST an exact JSON selection and return authenticated binary content."""
        async with self._request_client() as client:
            response = await client.post(path, json=payload, headers=self._headers())
            _raise_for_status(response)
            return HttpBinaryResponse(content=response.content, headers=dict(response.headers))

    async def post_framed_file(
        self,
        path: str,
        *,
        control: JsonObject,
        stream: BinaryIO,
        size_bytes: int,
        source: bytes | None = None,
    ) -> JsonObject:
        """POST a bounded canonical control frame followed by raw file bytes."""
        control_bytes = json.dumps(
            control,
            ensure_ascii=True,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('ascii')
        prefix = len(control_bytes).to_bytes(4, byteorder='big')
        content_length = len(prefix) + len(control_bytes) + size_bytes + len(source or b'')

        async def body() -> AsyncGenerator[bytes]:
            yield prefix
            yield control_bytes
            while chunk := stream.read(64 * 1024):
                yield chunk
            if source is not None:
                yield source

        headers = {
            **self._headers(),
            'Accept': 'application/json',
            'Content-Type': 'application/vnd.maivn.tool-file+binary',
            'Content-Length': str(content_length),
        }
        async with self._request_client() as client:
            response = await client.post(path, content=body(), headers=headers)
            return _response_object(response)

    async def patch(self, path: str, payload: JsonObject) -> JsonObject:
        """PATCH a JSON object and return a JSON object."""
        async with self._client() as client:
            response = await client.patch(path, json=payload, headers=self._headers())
            return _response_object(response)

    async def delete(self, path: str) -> JsonObject:
        """DELETE a JSON object and return a JSON object."""
        async with self._client() as client:
            response = await client.delete(path, headers=self._headers())
            return _response_object(response)

    async def get(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JsonObject:
        """GET a JSON object."""
        async with self._client() as client:
            response = await client.get(
                path,
                params=params,
                headers={**self._headers(), **dict(headers or {})},
            )
            return _response_object(response)

    async def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> HttpBinaryResponse:
        """GET bounded response bytes and return only allowlisted transport facts."""
        async with self._client() as client:
            response = await client.get(
                path,
                params=params,
                headers={**self._headers(), 'Accept': 'application/octet-stream'},
            )
            _raise_for_status(response)
            return HttpBinaryResponse(
                content=response.content,
                headers=dict(response.headers),
            )

    async def stream(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Open a streaming GET response.

        The caller owns closing the returned response.
        """
        client = self._client()
        request = client.build_request(
            'GET',
            path,
            params=params,
            headers={**self._headers(), **dict(headers or {}), 'Accept': 'text/event-stream'},
        )
        response: httpx.Response | None = None
        try:
            response = await client.send(request, stream=True)
            _raise_for_status(response)
        except BaseException:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                await client.aclose()
            raise
        response.extensions['maivn_client'] = client
        return response

    def _client(self) -> httpx.AsyncClient:
        if self._tracing is not None:
            return _TracingAsyncClient(
                tracing=self._tracing,
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=lease_transport(self._transport),
                event_hooks=self._tracing.event_hooks,
            )
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            transport=lease_transport(self._transport),
        )

    @asynccontextmanager
    async def _request_client(self) -> AsyncGenerator[httpx.AsyncClient]:
        """Yield the bounded shared client or one request-scoped fallback client."""
        if self._shared_client is not None:
            yield self._shared_client
            return
        async with self._client() as client:
            yield client

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self._api_key}',
            'Content-Type': 'application/json',
        }


def _response_object(response: httpx.Response) -> JsonObject:
    _raise_for_status(response)
    if not response.content:
        return {}
    value = response.json()
    if isinstance(value, dict):
        return cast('JsonObject', value)
    message = 'expected JSON object response from mAIvn API'
    raise MaivnHTTPError(status_code=response.status_code, reason=message)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < HTTP_ERROR_MIN_STATUS:
        return
    # Every API error reply is one envelope: `detail` (message), `code`, and
    # optionally `fields`, `retry_after` and `context`.
    body = _error_body(response)
    reason = _string_field(body, 'detail') or response.reason_phrase
    code = _string_field(body, 'code')
    context = body.get('context') if body is not None else None
    fields = _fields(body)
    retry_after = body.get('retry_after') if body is not None else None
    error_type = PlanLimitExceededError if code == PLAN_LIMIT_ERROR_CODE else MaivnHTTPError
    # Typed here, at the one place every non-2xx response passes through, so no
    # caller has to remember to inspect `code` before deciding whether to retry.
    # The status is carried through unchanged -- a classification, not a rewrite.
    raise error_type(
        status_code=response.status_code,
        reason=reason,
        code=code,
        detail=cast('JsonObject', context) if isinstance(context, dict) else None,
        fields=fields,
        retry_after=retry_after if isinstance(retry_after, int) else None,
    )


def _fields(body: JsonObject | None) -> list[dict[str, str]]:
    raw = body.get('fields') if body is not None else None
    if not isinstance(raw, list):
        return []
    fields: list[dict[str, str]] = []
    for item in cast('list[object]', raw):
        if isinstance(item, dict):
            entry = cast('dict[str, object]', item)
            field = entry.get('field')
            message = entry.get('message')
            if isinstance(field, str) and isinstance(message, str):
                fields.append({'field': field, 'message': message})
    return fields


def _error_body(response: httpx.Response) -> JsonObject | None:
    try:
        value = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return None
    if isinstance(value, dict):
        return cast('JsonObject', value)
    return None


def _string_field(value: JsonObject | None, key: str) -> str | None:
    if value is None:
        return None
    raw = value.get(key)
    if isinstance(raw, str) and raw:
        return raw
    return None


async def close_stream_response(response: httpx.Response) -> None:
    """Close a streaming response and its owning transient client."""
    client = response.extensions.get('maivn_client')
    await response.aclose()
    if isinstance(client, httpx.AsyncClient):
        await client.aclose()


@dataclass(frozen=True, slots=True)
class HttpBinaryResponse:
    """Response bytes plus headers, with request URL and auth intentionally discarded."""

    content: bytes
    headers: Mapping[str, str]


__all__ = [
    'HttpBinaryResponse',
    'HttpJsonClient',
    'JsonObject',
    'JsonValue',
    'close_stream_response',
]
