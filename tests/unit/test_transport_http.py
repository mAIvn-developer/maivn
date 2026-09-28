"""HTTP transport connection-pool behavior."""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import httpx

from maivn._internal.transport.http import HttpJsonClient


class _CountingHttpJsonClient(HttpJsonClient):
    """HTTP client exposing how many connection pools it creates."""

    created_clients: int = 0

    def _client(self) -> httpx.AsyncClient:
        self.created_clients += 1
        return super()._client()


def test_bounded_context_reuses_one_async_client() -> None:
    """Concurrent tool-result posts share one invocation-scoped connection pool."""
    client = _CountingHttpJsonClient(
        base_url='http://testserver',
        api_key='test-key',
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True}),
        ),
    )

    async def exercise() -> None:
        async with client:
            await asyncio.gather(
                client.post('/one', {'value': 1}),
                client.post('/two', {'value': 2}),
            )

    asyncio.run(exercise())

    assert client.created_clients == 1
