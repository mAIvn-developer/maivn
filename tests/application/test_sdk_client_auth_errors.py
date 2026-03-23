from __future__ import annotations

import httpx
import pytest

from maivn._internal.api.client import Client
from maivn._internal.core.exceptions import ServerAuthenticationError


class _StubHttpClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def request(
        self, method: str, url: str, *, headers: dict[str, str], json: dict
    ) -> httpx.Response:  # noqa: ANN001
        _ = (method, url, headers, json)
        return self._response


def test_client_raises_friendly_error_when_auth_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8000/start-session")
    response = httpx.Response(
        401,
        json={
            "detail": {
                "error": "missing_header",
                "message": "Authorization header or X-API-Key header is required",
            }
        },
        request=request,
    )

    client = Client(api_key=None)
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: _StubHttpClient(response))

    with pytest.raises(ServerAuthenticationError) as exc:
        client.start_session(payload={"state": {}, "client_id": "c1"})

    msg = str(exc.value)
    assert "MAIVN_API_KEY" in msg or "MAIVN_DEV_API_KEY" in msg
    assert "MAIVN_ALLOW_MOCK_USER" in msg


def test_client_raises_friendly_error_when_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8000/start-session")
    response = httpx.Response(
        401,
        json={
            "detail": {
                "error": "invalid_key",
                "message": "Invalid API key",
            }
        },
        request=request,
    )

    client = Client(api_key="bad-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: _StubHttpClient(response))

    with pytest.raises(ServerAuthenticationError) as exc:
        client.start_session(payload={"state": {}, "client_id": "c1"})

    msg = str(exc.value)
    assert "rejected" in msg.lower() or "invalid" in msg.lower()
    assert "Developer Portal" in msg
