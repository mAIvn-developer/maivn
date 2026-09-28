"""Developer-owned Postgres sender binding and secret custody."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from maivn.postgres import postgres_sender_plan

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

RECEIVER = 'https://receiver.example.com/v1/connections/conn_orders/events'
MATERIAL = 'synthetic-private-test-material-123456789'


@pytest.mark.parametrize(
    'table', ['orders', 'public.orders.extra', 'public.orders;DROP TABLE x', 'public."orders"']
)
def test_invalid_source_is_refused(table: str) -> None:
    """A source cannot become unreviewed SQL or an ambiguous relation."""
    with pytest.raises(ValueError, match=r'schema\.table'):
        postgres_sender_plan(table=table, columns=['id'], receiver_url=RECEIVER)


@pytest.mark.parametrize('columns', [[], ['id', 'id'], ['*'], ['id); SELECT 1'], ['']])
def test_invalid_projection_is_refused(columns: list[str]) -> None:
    """The sender needs a finite, unambiguous column allowlist."""
    with pytest.raises(ValueError, match='columns'):
        postgres_sender_plan(table='public.orders', columns=columns, receiver_url=RECEIVER)


@pytest.mark.parametrize(
    'url',
    [
        'http://receiver.example.com/v1/connections/conn_orders/events',
        'https://user:pass@receiver.example.com/v1/connections/conn_orders/events',
        RECEIVER + '?other=1',
        RECEIVER + '#fragment',
        RECEIVER + '/extra',
        'https://receiver.example.com/v1/connections/conn_%27/events',
    ],
)
def test_invalid_receiver_is_refused(url: str) -> None:
    """The signing target is one explicit HTTPS canonical receiver."""
    with pytest.raises(ValueError, match='receiver'):
        postgres_sender_plan(table='public.orders', columns=['id'], receiver_url=url)


class Database:
    """Record transaction behavior without accepting interpolated secrets."""

    def __init__(self, *, fail_binding: bool = False) -> None:
        """Select whether private binding is refused."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail_binding = fail_binding
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None]:
        """Record atomic completion or rollback."""
        try:
            yield
        except RuntimeError:
            self.rolled_back = True
            raise
        else:
            self.committed = True

    async def execute(self, query: str, *args: object) -> str:
        """Accept SQL separately from private parameters."""
        self.calls.append((query, args))
        if args and self.fail_binding:
            message = 'binding refused'
            raise RuntimeError(message)
        return 'OK'


def test_secret_is_bound_only_inside_atomic_install() -> None:
    """A generated plan is reviewable without containing the signing secret."""
    plan = postgres_sender_plan(
        table='public.orders', columns=['id', 'status'], receiver_url=RECEIVER
    )
    secret = MATERIAL
    database = Database()
    asyncio.run(plan.install(database, secret=secret))
    assert database.committed
    assert not database.rolled_back
    assert [args for _, args in database.calls if args] == [(secret,)]
    assert all(secret not in query for query, _ in database.calls)
    assert secret not in repr(plan)
    assert database.calls[-1][0] == plan.attach_sql


def test_binding_failure_rolls_back_without_attaching() -> None:
    """No live source trigger survives failed secret provisioning."""
    plan = postgres_sender_plan(table='public.orders', columns=['id'], receiver_url=RECEIVER)
    database = Database(fail_binding=True)
    with pytest.raises(RuntimeError, match='binding refused'):
        asyncio.run(plan.install(database, secret=MATERIAL))
    assert database.rolled_back
    assert not database.committed
    assert all(query != plan.attach_sql for query, _ in database.calls)
