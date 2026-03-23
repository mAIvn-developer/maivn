from __future__ import annotations

from typing import Any

import httpx
import pytest

from maivn._internal.core.application_services.http.http_client_service import (
    HttpClientService,
)


class _StubSessionClient:
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer test"}


class _NullLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None


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
        logger=_NullLogger(),
    )

    service.post_resume(
        url="http://example.local/resume",
        payload={"result": "ok"},
        client=_StubSessionClient(),
    )

    assert len(calls) == 2


def test_http_client_service_stops_on_connection_refused() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        err = OSError("Connection refused")
        err.winerror = 10061  # type: ignore[attr-defined]
        exc = httpx.ConnectError("Connection refused", request=request)
        exc.__cause__ = err
        raise exc

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    service = HttpClientService(
        timeout=1.0,
        max_retries=3,
        http_client=client,
        logger=_NullLogger(),
    )

    with pytest.raises(httpx.ConnectError):
        service.post_resume(
            url="http://example.local/resume",
            payload={"result": "ok"},
            client=_StubSessionClient(),
        )

    assert len(calls) == 1
