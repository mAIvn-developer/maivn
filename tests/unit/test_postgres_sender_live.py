"""Opt-in rollback proofs on the explicitly prepared disposable TR2 database.

Requires asyncpg and the `postgres` database of a throwaway loopback server
prepared with tests/fixtures/postgres_sender_live.sql:
MAIVN_TEST_POSTGRES_SENDER_DSN connects as a superuser owner and
MAIVN_TEST_POSTGRES_SENDER_OWNER_DSN as a non-superuser CREATEROLE owner.
No network request can leave the database because the source writes and pg_net
queue entries always roll back.
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
        # The installer acted as the sender role only while installing.
        assert not await database.fetchval(
            "SELECT pg_has_role(current_user, 'maivn_db_sender', 'SET') AND NOT rolsuper "
            'FROM pg_catalog.pg_roles WHERE rolname = current_user'
        )
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
        rebound = database.transaction()
        await rebound.start()
        try:
            # The owner keeps the right to act as the role it created, as the
            # installer did, so it can let itself attach the function again.
            await database.execute(
                'GRANT maivn_db_sender TO CURRENT_USER;'
                'GRANT EXECUTE ON FUNCTION maivn_sender_private.send_change() TO CURRENT_USER;'
                'CREATE TRIGGER copied_sender AFTER INSERT ON public.tr2_other_rows '
                'FOR EACH ROW EXECUTE FUNCTION maivn_sender_private.send_change()'
            )
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


def _prepared_dsn(variable: str) -> str:
    dsn = os.environ.get(variable)
    if not dsn:
        pytest.skip(f'Set {variable} for the prepared isolated sender database')
    assert urlsplit(dsn).hostname in {'127.0.0.1', 'localhost', '::1'}
    return dsn


async def owner_attributes(dsn: str, driver: Any) -> tuple[bool, bool]:
    """Return (superuser, createrole) for the role the DSN logs in as."""
    database = await driver.connect(dsn)
    try:
        row = await database.fetchrow(
            'SELECT rolsuper, rolcreaterole FROM pg_catalog.pg_roles WHERE rolname = current_user'
        )
        assert row is not None
        return row['rolsuper'], row['rolcreaterole']
    finally:
        await database.close()


def test_real_postgres_sender_rollback() -> None:
    """Opt in only to the named disposable loopback target prepared by the owner."""
    dsn = _prepared_dsn('MAIVN_TEST_POSTGRES_SENDER_DSN')
    driver = pytest.importorskip('asyncpg')
    assert asyncio.run(owner_attributes(dsn, driver))[0], 'this proof installs as a superuser owner'
    asyncio.run(prove(dsn, driver))


def test_real_postgres_sender_installs_as_non_superuser_owner() -> None:
    """A CREATEROLE owner that is not a superuser (Supabase's `postgres`) can install it."""
    dsn = _prepared_dsn('MAIVN_TEST_POSTGRES_SENDER_OWNER_DSN')
    driver = pytest.importorskip('asyncpg')
    assert asyncio.run(owner_attributes(dsn, driver)) == (False, True)
    asyncio.run(prove(dsn, driver))
