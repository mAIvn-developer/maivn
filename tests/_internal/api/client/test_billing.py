# pyright: strict
from __future__ import annotations

from collections import deque

import httpx
import pytest

from maivn import BillingUsage, Client


class _QueueHttpClient:
    _responses: deque[dict[str, object]]
    calls: list[dict[str, object]]

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses = deque(responses)
        self.calls = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object] | None,
    ) -> httpx.Response:
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        payload = self._responses.popleft()
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))


def _usage_payload() -> dict[str, object]:
    return {
        "organization_id": "org-1",
        "period_start": "2026-05-01T00:00:00+00:00",
        "period_end": "2026-05-31T23:59:59.999999+00:00",
        "plan": {"name": "beta_tester", "display_name": "Beta", "tier": "beta_tester"},
        "usage": {
            "tokens_used": 1_314_000,
            "tokens_used_raw": 1_200_000,
            "tokens_limit": 50_000_000,
            "requests_used": 879,
            "requests_limit": 1_500_000,
            "storage_used": 42,
            "storage_limit": 10_000_000_000,
        },
        "breakdown": {
            "organization_id": "org-1",
            "period_start": "2026-05-01T00:00:00+00:00",
            "period_end": "2026-05-31T23:59:59.999999+00:00",
            "totals": {"billed_tokens": 1_314_000, "raw_tokens": 1_200_000, "requests": 879},
            "projects": [
                {
                    "project_id": "p1",
                    "project_name": "MAIVN Development",
                    "billed_tokens": 1_314_000,
                    "raw_tokens": 1_200_000,
                    "requests": 879,
                    "api_keys": [
                        {
                            "api_key_id": "k1",
                            "api_key_name": "Production key",
                            "billed_tokens": 1_250_000,
                            "raw_tokens": 1_150_000,
                            "requests": 842,
                        },
                        {
                            "api_key_id": None,
                            "api_key_name": "Unknown API key",
                            "billed_tokens": 64_000,
                            "raw_tokens": 50_000,
                            "requests": 37,
                        },
                    ],
                }
            ],
        },
    }


def _client(monkeypatch: pytest.MonkeyPatch, http_client: _QueueHttpClient) -> Client:
    client = Client(api_key="test-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: http_client)
    return client


def test_get_usage_returns_typed_model_for_period(monkeypatch: pytest.MonkeyPatch) -> None:
    http_client = _QueueHttpClient([_usage_payload()])
    client = _client(monkeypatch, http_client)

    usage = client.get_usage(year=2026, month=5)

    assert isinstance(usage, BillingUsage)
    assert usage.organization_id == "org-1"
    assert usage.plan is not None
    assert usage.plan.tier == "beta_tester"
    assert usage.usage.tokens_used == 1_314_000
    assert usage.usage.tokens_limit == 50_000_000
    assert usage.breakdown.totals.billed_tokens == 1_314_000
    # The unattributed-key bucket is preserved through the typed model.
    assert usage.breakdown.projects[0].api_keys[-1].api_key_name == "Unknown API key"
    assert usage.breakdown.projects[0].api_keys[-1].api_key_id is None

    call = http_client.calls[0]
    assert call["method"] == "GET"
    url = call["url"]
    assert isinstance(url, str)
    assert url.endswith("/billing/usage?year=2026&month=5")
    headers = call["headers"]
    assert isinstance(headers, dict)
    assert headers["X-API-Key"] == "test-key"


def test_get_usage_defaults_to_current_period_without_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = _QueueHttpClient([_usage_payload()])
    client = _client(monkeypatch, http_client)

    _ = client.get_usage()

    url = http_client.calls[0]["url"]
    assert isinstance(url, str)
    assert url.endswith("/billing/usage")
