"""Reviewable, explicitly installed signing senders for developer-owned Postgres."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager

_IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}')
_NAME = re.compile(r'[a-z][a-z0-9_]{0,39}')
_RECEIVER = re.compile(
    r'(?P<origin>https://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d{1,5})?)'
    r'/v1/connections/(?P<connection>[A-Za-z0-9][A-Za-z0-9._:-]{0,199})/events'
)
_MIN_SECRET_BYTES = 32
_MAX_COLUMNS = 64
_TABLE_PARTS = 2


class PostgresConnection(Protocol):
    """The async transaction/execute surface provided by asyncpg connections."""

    def transaction(self) -> AbstractAsyncContextManager[object]:
        """Open one atomic transaction."""
        ...

    async def execute(self, query: str, *args: object) -> object:
        """Execute SQL with positional parameters, without logging their values."""
        ...


@dataclass(frozen=True)
class PostgresSenderPlan:
    """Non-secret SQL to review before explicitly installing a bound sender.

    The caller supplies a database-owner connection. No database is discovered,
    no extension is installed, and no shared pg_net permission is changed.
    Installation fails atomically if the name already exists. Existing queue
    grants require an owner review before adding another sender to this database.
    """

    prepare_sql: str
    bind_sql: str
    attach_sql: str

    async def install(self, connection: PostgresConnection, *, secret: str) -> None:
        """Apply the reviewed plan atomically, binding the secret separately.

        Keep driver and server parameter logging disabled. pg_net uses an
        unlogged queue and is not a durable outbox. The receiver requires delivery
        within its signing window; a long source transaction can expire it.
        """
        if len(secret.encode('utf-8')) < _MIN_SECRET_BYTES:
            message = 'The connection signing secret must contain at least 32 UTF-8 bytes.'
            raise ValueError(message)
        async with connection.transaction():
            await connection.execute(self.prepare_sql)
            await connection.execute(self.bind_sql, secret)
            await connection.execute(self.attach_sql)


def postgres_sender_plan(
    *, table: str, columns: Sequence[str], receiver_url: str, name: str = 'maivn'
) -> PostgresSenderPlan:
    """Bind one schema.table, explicit column projection and HTTPS receiver.

    Identifiers use simple ASCII names. Quoted/mixed syntax, wildcard columns,
    credentials in URLs and arbitrary callback paths are refused. The generated
    trigger validates its relation OID as well as names on each change.
    """
    parts = table.split('.')
    if len(parts) != _TABLE_PARTS or any(_IDENTIFIER.fullmatch(part) is None for part in parts):
        message = 'table must be a simple schema.table identifier.'
        raise ValueError(message)
    if (
        isinstance(columns, str)
        or not 1 <= len(columns) <= _MAX_COLUMNS
        or any(_IDENTIFIER.fullmatch(column) is None for column in columns)
        or len(set(columns)) != len(columns)
    ):
        message = 'columns must be 1 to 64 unique, explicit simple identifiers.'
        raise ValueError(message)
    receiver = _RECEIVER.fullmatch(receiver_url)
    if receiver is None:
        message = 'receiver_url must be one canonical HTTPS connection receiver URL.'
        raise ValueError(message)
    if _NAME.fullmatch(name) is None:
        message = (
            'name must start with a lowercase letter; use up to 40 letters/digits/underscores.'
        )
        raise ValueError(message)
    schema, relation = parts
    replacements = {
        '__SOURCE_SCHEMA__': schema,
        '__SOURCE_TABLE__': relation,
        '__APPROVED_HTTPS_ORIGIN__': receiver['origin'],
        '__CONNECTION_ID__': receiver['connection'],
        '__APPROVED_COLUMNS__': ', '.join(f"'{column}'" for column in columns),
        '__SENDER_ROLE__': name + '_db_sender',
        '__PRIVATE_SCHEMA__': name + '_sender_private',
    }
    prepare = files('maivn').joinpath('_postgres_sender.sql').read_text(encoding='utf-8')
    for placeholder, value in replacements.items():
        prepare = prepare.replace(placeholder, value)
    private = replacements['__PRIVATE_SCHEMA__']
    source = f'"{schema}"."{relation}"'
    bind = (
        f'INSERT INTO {private}.config(singleton, source_relation, secret) '  # noqa: S608 - Validated simple identifiers; secret binds as $1.
        f"VALUES (true, '{source}'::pg_catalog.regclass::oid, pg_catalog.convert_to($1, 'UTF8'));"
    )
    attach = (
        f'GRANT USAGE ON SCHEMA {private} TO CURRENT_USER;\n'
        f'GRANT EXECUTE ON FUNCTION {private}.send_change() TO CURRENT_USER;\n'
        f'CREATE TRIGGER {name}_signed_change AFTER INSERT OR UPDATE OR DELETE ON {source}\n'
        f'FOR EACH ROW EXECUTE FUNCTION {private}.send_change();\n'
        f'REVOKE EXECUTE ON FUNCTION {private}.send_change() FROM CURRENT_USER;\n'
    )
    return PostgresSenderPlan(prepare_sql=prepare, bind_sql=bind, attach_sql=attach)
