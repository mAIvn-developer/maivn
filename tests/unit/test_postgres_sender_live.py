"""Opt-in rollback proof on the explicitly prepared disposable TR2 database.

Requires asyncpg and MAIVN_TEST_POSTGRES_SENDER_DSN. No network request can leave
the database because the source writes and pg_net queue entries always roll back.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest

from maivn.postgres import postgres_sender_plan

if TYPE_CHECKING:
    from typing import Any

MATERIAL = 'synthetic-postgres-signing-test-material-123456789'
CALLBACK = 'https://receiver.example.com/v1/connections/conn_rollback/events'
PORT = 55442


async def prove(dsn: str, driver: Any) -> None:
    """Execute generated SQL and source writes against actual PostgreSQL."""
    database = await driver.connect(dsn)
    outer = database.transaction()
    await outer.start()
    try:
        assert await database.fetchval('SELECT current_database()') == 'postgres'
        assert await database.fetchval("SELECT to_regclass('brain.sessions')") is None
        plan = postgres_sender_plan(
            table='public.tr2_source_rows', columns=['id', 'status'], receiver_url=CALLBACK
        )
        missing = postgres_sender_plan(
            table='public.tr2_source_rows', columns=['nonexistent'], receiver_url=CALLBACK
        )
        with pytest.raises(driver.RaiseError, match='selected column'):
            await missing.install(database, secret=MATERIAL)
        await database.execute('GRANT USAGE ON SCHEMA net TO PUBLIC')
        with pytest.raises(driver.RaiseError, match='PUBLIC access'):
            await plan.install(database, secret=MATERIAL)
        await database.execute('REVOKE ALL ON SCHEMA net FROM PUBLIC')
        await plan.install(database, secret=MATERIAL)
        await database.execute('SET LOCAL ROLE tr2_source_writer')
        await database.execute(
            "INSERT INTO public.tr2_source_rows VALUES (8101, 'new', 'private must stay local')"
        )
        await database.execute('RESET ROLE')
        row = await database.fetchrow(
            'SELECT url, headers, body FROM net.http_request_queue ORDER BY id DESC LIMIT 1'
        )
        assert row is not None
        assert row['url'] == CALLBACK
        body = bytes(row['body'])
        payload = json.loads(body)
        assert payload['record'] == {'id': 8101, 'status': 'new'}
        assert payload['old_record'] is None
        assert payload['type'] == 'INSERT'
        assert 'private' not in body.decode()
        timestamp, digest = json.loads(row['headers'])['X-Maivn-Signature'].split(',')
        signed = (
            b'POST./v1/connections/conn_rollback/events.' + timestamp[2:].encode() + b'.' + body
        )
        assert digest == 'v1=' + hmac.new(MATERIAL.encode(), signed, hashlib.sha256).hexdigest()
        assert payload['delivery_id']
        # A trusted owner rebinding the function cannot bypass its table guard.
        await database.execute(
            'CREATE TRIGGER copied_sender AFTER INSERT ON public.tr2_other_rows '
            'FOR EACH ROW EXECUTE FUNCTION maivn_sender_private.send_change()'
        )
        rebound = database.transaction()
        await rebound.start()
        try:
            await database.execute('SET LOCAL ROLE tr2_source_writer')
            with pytest.raises(driver.RaiseError, match='binding mismatch'):
                await database.execute(
                    "INSERT INTO public.tr2_other_rows VALUES (8101, 'wrong', '')"
                )
        finally:
            await rebound.rollback()
    finally:
        await outer.rollback()
        assert await database.fetchval("SELECT to_regclass('maivn_sender_private.config')") is None
        assert await database.fetchval('SELECT count(*) FROM public.tr2_source_rows') == 0
        assert await database.fetchval('SELECT count(*) FROM net.http_request_queue') == 0
        await database.close()


def test_real_postgres_sender_rollback() -> None:
    """Opt in only to the named disposable loopback target prepared by the owner."""
    dsn = os.environ.get('MAIVN_TEST_POSTGRES_SENDER_DSN')
    if not dsn:
        pytest.skip('Set MAIVN_TEST_POSTGRES_SENDER_DSN for the prepared isolated sender database')
    target = urlsplit(dsn)
    assert target.hostname == '127.0.0.1'
    assert target.port == PORT
    driver = pytest.importorskip('asyncpg')
    asyncio.run(prove(dsn, driver))
