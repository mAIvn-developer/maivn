# pyright: strict
from __future__ import annotations

from typing import cast, final

import httpx
import pytest
from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.application_services.http.http_client_service import (
    HttpClientService,
)


@final
class _StubSessionClient:
    """Minimal ``SessionClientProtocol`` stub: only ``headers()`` is exercised."""

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}


@final
class _NullLogger:
    """No-op ``LoggerProtocol`` implementation for retry-path tests."""

    def debug(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (message, args, component, metadata)

    def info(self, message: str, *args: object, component: str = "APP", **metadata: object) -> None:
        _ = (message, args, component, metadata)

    def warning(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (message, args, component, metadata)

    def error(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (message, args, component, metadata)

    def exception(self, message: str, component: str = "APP", **metadata: object) -> None:
        _ = (message, component, metadata)

    def critical(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (message, args, component, metadata)


def _logger() -> LoggerProtocol:
    return cast(LoggerProtocol, _NullLogger())


def _session_client() -> SessionClientProtocol:
    # Pattern 2: double-cast for stub objects that intentionally implement only
    # the subset of the protocol exercised by ``post_resume`` (just ``headers``).
    return cast(SessionClientProtocol, cast(object, _StubSessionClient()))


def test_http_client_service_retries_on_server_error() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(500, request=request, json={"error": "fail"})
        return httpx.Response(200, request=request, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    service = HttpClientService(
        timeout=1.0,
        max_retries=2,
        http_client=client,
        logger=_logger(),
    )

    service.post_resume(
        url="http://example.local/resume",
        payload={"result": "ok"},
        client=_session_client(),
    )

    assert len(calls) == 2


def test_http_client_service_stops_on_connection_refused() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        err = OSError("Connection refused")
        # ``winerror`` is a Windows-only OSError extension; assigning via setattr
        # keeps strict pyright happy across platforms.
        err.winerror = 10061
        exc = httpx.ConnectError("Connection refused", request=request)
        exc.__cause__ = err
        raise exc

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    service = HttpClientService(
        timeout=1.0,
        max_retries=3,
        http_client=client,
        logger=_logger(),
    )

    with pytest.raises(httpx.ConnectError):
        service.post_resume(
            url="http://example.local/resume",
            payload={"result": "ok"},
            client=_session_client(),
        )

    assert len(calls) == 1
