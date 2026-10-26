"""Characterize the reviewable Postgres sender plan: rendering, quoting and secret custody."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from maivn.postgres import PostgresSenderPlan, postgres_sender_plan

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

_RECEIVER = 'https://hooks.example.com:8443/v1/connections/conn.Orders_1:a-b/events'
_SENTINEL = 'sentinel-signing-material-0123456789abcdef'
_TABLE_ERROR = r'^table must be a simple schema\.table identifier\.$'
_COLUMN_ERROR = r'^columns must be 1 to 64 unique, explicit simple identifiers\.$'
_RECEIVER_ERROR = r'^receiver_url must be one canonical HTTPS connection receiver URL\.$'
_NAME_ERROR = (
    r'^name must start with a lowercase letter; use up to 40 letters/digits/underscores\.$'
)
_SHORT_MATERIAL_ERROR = r'^The connection signing secret must contain at least 32 UTF-8 bytes\.$'


def _plan(
    *,
    table: str = 'public.orders',
    columns: Sequence[str] = ('id', 'status'),
    receiver_url: str = _RECEIVER,
    name: str = 'maivn',
) -> PostgresSenderPlan:
    return postgres_sender_plan(table=table, columns=columns, receiver_url=receiver_url, name=name)


class Connection:
    """Record SQL and parameters inside one transaction."""

    def __init__(self) -> None:
        """Start with no statements."""
        self.events: list[object] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[None]:
        """Record the transaction boundary."""
        self.events.append('begin')
        yield
        self.events.append('commit')

    async def execute(self, query: str, *args: object) -> str:
        """Record one statement with its parameters."""
        self.events.append((query, args))
        return 'OK'


def test_prepare_sql_binds_every_placeholder_to_validated_values() -> None:
    """The rendered preparation SQL contains the reviewed constants and no placeholders."""
    plan = _plan(table='Sales.Orders_2024', columns=['id', 'Status'], name='shop_1')
    prepare = plan.prepare_sql
    assert re.search(r'__[A-Z_]+__', prepare) is None
    assert 'pg_catalog.to_regclass(\'"Sales"."Orders_2024"\') IS NULL' in prepare
    assert "approved_schema CONSTANT text := 'Sales';" in prepare
    assert "approved_table CONSTANT text := 'Orders_2024';" in prepare
    assert "approved_origin CONSTANT text := 'https://hooks.example.com:8443';" in prepare
    assert "approved_connection CONSTANT text := 'conn.Orders_1:a-b';" in prepare
    assert "approved_columns CONSTANT text[] := ARRAY['id', 'Status'];" in prepare
    assert "unnest(ARRAY['id', 'Status']::text[])" in prepare
    assert 'CREATE ROLE shop_1_db_sender NOLOGIN NOINHERIT' in prepare
    assert 'CREATE SCHEMA shop_1_sender_private;' in prepare
    assert (
        'ALTER FUNCTION shop_1_sender_private.send_change() OWNER TO shop_1_db_sender;' in prepare
    )
    assert "sender oid := 'shop_1_db_sender'::pg_catalog.regrole::oid;" in prepare
    assert "WHERE n.nspname = 'shop_1_sender_private'" in prepare


def test_bind_and_attach_sql_are_exact_and_parameterized() -> None:
    """The secret binds only as $1 and attaching revokes the temporary grants."""
    plan = _plan()
    assert plan.bind_sql == (
        'INSERT INTO maivn_sender_private.config(singleton, source_relation, secret) '
        'VALUES (true, \'"public"."orders"\'::pg_catalog.regclass::oid, '
        "pg_catalog.convert_to($1, 'UTF8'));"
    )
    assert plan.attach_sql == (
        'GRANT USAGE ON SCHEMA maivn_sender_private TO CURRENT_USER;\n'
        'GRANT EXECUTE ON FUNCTION maivn_sender_private.send_change() TO CURRENT_USER;\n'
        'CREATE TRIGGER maivn_signed_change AFTER INSERT OR UPDATE OR DELETE ON '
        '"public"."orders"\n'
        'FOR EACH ROW EXECUTE FUNCTION maivn_sender_private.send_change();\n'
        'REVOKE EXECUTE ON FUNCTION maivn_sender_private.send_change() FROM CURRENT_USER;\n'
        'REVOKE maivn_db_sender FROM CURRENT_USER;\n'
    )


def test_identifier_length_boundaries() -> None:
    """Schema, table and column identifiers may be 63 characters, never 64."""
    longest = 'a' * 63
    assert f'"{longest}"."{longest}"' in _plan(table=f'{longest}.{longest}').attach_sql
    assert f"ARRAY['{longest}']" in _plan(columns=[longest]).prepare_sql
    with pytest.raises(ValueError, match=_TABLE_ERROR):
        _plan(table=f'public.{"a" * 64}')
    with pytest.raises(ValueError, match=_TABLE_ERROR):
        _plan(table=f'{"a" * 64}.orders')
    with pytest.raises(ValueError, match=_COLUMN_ERROR):
        _plan(columns=['a' * 64])


@pytest.mark.parametrize(
    'table',
    [
        'public.orders\n',
        'public.orders.extra',
        'public.ordérs',
        '1public.orders',
        'public.or-ders',
        'public.',
        '.orders',
        "public.o'rders",
    ],
)
def test_tables_that_could_escape_quoting_are_refused(table: str) -> None:
    """Every table part must be a plain ASCII identifier with no trailing newline."""
    with pytest.raises(ValueError, match=_TABLE_ERROR):
        _plan(table=table)


def test_column_count_boundaries() -> None:
    """Between one and sixty-four distinct columns are accepted."""
    columns = [f'c{index}' for index in range(64)]
    assert "'c63'];" in _plan(columns=columns).prepare_sql
    with pytest.raises(ValueError, match=_COLUMN_ERROR):
        _plan(columns=[*columns, 'c64'])


@pytest.mark.parametrize('columns', ['id', ('id', 'ID', 'id'), ("o'clock",), ('id\n',)])
def test_ambiguous_column_selections_are_refused(columns: Sequence[str]) -> None:
    """A bare string, duplicates, quotes or newlines never become the projection."""
    with pytest.raises(ValueError, match=_COLUMN_ERROR):
        _plan(columns=columns)


def test_case_distinct_columns_are_distinct() -> None:
    """Columns differing only by case are different identifiers."""
    assert "ARRAY['id', 'ID']" in _plan(columns=('id', 'ID')).prepare_sql


@pytest.mark.parametrize(
    'url',
    [
        'https://localhost/v1/connections/conn/events',
        'https://hooks.example.com:123456/v1/connections/conn/events',
        'https://hooks_x.example.com/v1/connections/conn/events',
        'https://hooks.example.com/v1/connections/.conn/events',
        'https://hooks.example.com/v1/connections/conn/events\n',
        'https://hooks.example.com/v1/connections/' + 'c' * 201 + '/events',
        'https://hooks.example.com/v2/connections/conn/events',
        'HTTPS://hooks.example.com/v1/connections/conn/events',
    ],
)
def test_non_canonical_receivers_are_refused(url: str) -> None:
    """Single-label hosts, long ports, odd labels and long connection ids are refused."""
    with pytest.raises(ValueError, match=_RECEIVER_ERROR):
        _plan(receiver_url=url)


def test_receiver_boundaries_are_accepted() -> None:
    """A five-digit port and a 200-character connection id are accepted."""
    connection = 'c' * 200
    plan = _plan(
        receiver_url=f'https://a.b:65535/v1/connections/{connection}/events',
    )
    assert "approved_origin CONSTANT text := 'https://a.b:65535';" in plan.prepare_sql
    assert f"approved_connection CONSTANT text := '{connection}';" in plan.prepare_sql


@pytest.mark.parametrize('name', ['', 'Maivn', '1maivn', '_maivn', 'mai-vn', 'm' * 41])
def test_invalid_sender_names_are_refused(name: str) -> None:
    """Sender names are lowercase identifiers of at most forty characters."""
    with pytest.raises(ValueError, match=_NAME_ERROR):
        _plan(name=name)


def test_longest_sender_name_is_accepted() -> None:
    """A forty-character name yields role, schema and trigger names from it."""
    name = 'n' * 40
    plan = _plan(name=name)
    assert f'CREATE TRIGGER {name}_signed_change ' in plan.attach_sql
    assert f'REVOKE {name}_db_sender FROM CURRENT_USER;' in plan.attach_sql


def test_install_runs_three_statements_in_one_transaction() -> None:
    """Prepare, bind and attach run in order inside one transaction; only bind has a value."""
    plan = _plan()
    connection = Connection()
    asyncio.run(plan.install(connection, secret=_SENTINEL))
    assert connection.events == [
        'begin',
        (plan.prepare_sql, ()),
        (plan.bind_sql, (_SENTINEL,)),
        (plan.attach_sql, ()),
        'commit',
    ]
    assert _SENTINEL not in repr(plan)
    assert all(_SENTINEL not in sql for sql in (plan.prepare_sql, plan.bind_sql, plan.attach_sql))


@pytest.mark.parametrize('secret', ['s' * 32, 'é' * 16])
def test_secret_of_32_utf8_bytes_is_accepted(secret: str) -> None:
    """The minimum is measured in UTF-8 bytes, not characters."""
    connection = Connection()
    asyncio.run(_plan().install(connection, secret=secret))
    assert connection.events[-1] == 'commit'


@pytest.mark.parametrize('secret', ['s' * 31, 'é' * 15 + 's'])
def test_short_secret_is_refused_before_any_statement(secret: str) -> None:
    """A secret under 32 UTF-8 bytes never opens a transaction and is not echoed."""
    connection = Connection()
    with pytest.raises(ValueError, match=_SHORT_MATERIAL_ERROR) as caught:
        asyncio.run(_plan().install(connection, secret=secret))
    assert connection.events == []
    assert secret not in str(caught.value)
