"""The sender install refuses a signing secret that is not text, before any SQL (U05 minor)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

import pytest

from maivn.postgres import postgres_sender_plan

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_RECEIVER = 'https://hooks.example.com:8443/v1/connections/conn.Orders_1:a-b/events'


class _Connection:
    def __init__(self) -> None:
        self.events: list[object] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None]:
        self.events.append('begin')
        yield
        self.events.append('commit')

    async def execute(self, query: str, *args: object) -> str:
        self.events.append((query, args))
        return 'OK'


@pytest.mark.parametrize('secret', [b'x' * 40, 12345678901234567890123456789012345, None])
def test_a_non_text_secret_is_refused_before_the_transaction(secret: object) -> None:
    """Bytes, numbers or None fail with a clear message and touch nothing."""
    plan = postgres_sender_plan(
        table='public.orders', columns=['id'], receiver_url=_RECEIVER, name='maivn'
    )
    connection = _Connection()

    with pytest.raises(TypeError, match=r'^The connection signing secret must be a string\.$'):
        asyncio.run(plan.install(connection, secret=cast('str', secret)))

    assert connection.events == []
