"""Automatic storage preserves identity without touching unrelated SDK calls."""

# ruff: noqa: SLF001, PLR2004
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest

from maivn import Client, LocalVaultConfig, VaultSetupError
from maivn._internal import automatic_vault

_USER = '00000000-0000-0000-0000-000000000001'
_ORG = '00000000-0000-0000-0000-000000000002'
_PROJECT = '00000000-0000-0000-0000-000000000003'


class MissingRecordError(Exception):
    """Fake native missing-record boundary."""


class NativeVault:
    """Fake native boundary; stores remain isolated by authenticated scope."""

    records: ClassVar[dict[tuple[str, str], bytes]] = {}
    scopes: ClassVar[set[str]] = set()
    opens: ClassVar[int] = 0

    def __init__(self, scope: str) -> None:
        """Bind the selected account scope."""
        self.scope = scope

    @classmethod
    def default_exists(cls, scope: str | None = None, *, path: str | None = None) -> bool:
        """Inspect metadata without acquiring a key."""
        _ = path
        return bool(cls.scopes) if scope is None else scope in cls.scopes

    @classmethod
    def open_default(
        cls,
        scope: str,
        *,
        path: str | None = None,
        secret: bytes | None = None,
    ) -> NativeVault:
        """Open the selected scope."""
        _ = path, secret
        cls.opens += 1
        cls.scopes.add(scope)
        return cls(scope)

    def merge_value_map(self, tenant: str, record: str, payload: bytes) -> None:
        """Merge under native account and record identity."""
        _ = tenant
        key = (self.scope, record)
        prior = json.loads(self.records.get(key, b'{}'))
        prior.update(json.loads(payload))
        self.records[key] = json.dumps(prior).encode()

    def load_value_map(self, tenant: str, record: str) -> bytes:
        """Return data or the native missing-record error."""
        _ = tenant
        try:
            return self.records[self.scope, record]
        except KeyError:
            raise MissingRecordError from None

    def purge(self, tenant: str, record: str) -> None:
        """Remove the selected record."""
        _ = tenant
        self.records.pop((self.scope, record), None)


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> type[NativeVault]:
    """Keep the credential store untouched while testing SDK wiring."""
    NativeVault.records = {}
    NativeVault.scopes = set()
    NativeVault.opens = 0
    module = SimpleNamespace(LocalVault=NativeVault, RecordNotFound=MissingRecordError)
    monkeypatch.setattr(automatic_vault, '_native', lambda: module)
    monkeypatch.setattr('maivn._internal.private_data_store._vault_module', lambda: module)
    return NativeVault


def transport(project: str = _PROJECT) -> httpx.MockTransport:
    """Return authenticated identity without a database or live service."""

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/vault/identity'
        return httpx.Response(200, json={'user_id': _USER, 'org_id': _ORG, 'project_id': project})

    return httpx.MockTransport(respond)


def test_default_is_lazy_and_resumes_across_clients_and_key_rotation(
    native: type[NativeVault],
) -> None:
    """Stable authenticated scope survives client and API-key replacement."""

    async def run() -> None:
        first = Client(api_key='old', transport=transport())
        assert native.opens == 0
        assert await first._thread_private_data('thread', {'name': 'Ada'}) == {'name': 'Ada'}
        second = Client(api_key='rotated', transport=transport())
        assert await second._thread_private_data('thread', None) == {'name': 'Ada'}
        other = Client(api_key='other', transport=transport('00000000-0000-0000-0000-000000000004'))
        assert await other._thread_private_data('thread', None) is None
        assert native.opens == 2

    asyncio.run(run())


def test_empty_new_user_and_explicit_opt_out_do_not_open_vault(native: type[NativeVault]) -> None:
    """A call that needs no storage does not contact identity or credentials."""

    async def run() -> None:
        client = Client(api_key='test')
        assert await client._thread_private_data('thread', None) is None
        opted_out = Client(api_key='test', private_data_store=None)
        assert await opted_out._thread_private_data('thread', {'name': 'Ada'}) == {'name': 'Ada'}
        assert native.opens == 0

    asyncio.run(run())


def test_invalid_identity_fails_before_native_open(native: type[NativeVault]) -> None:
    """An untrusted or incomplete identity cannot select storage."""
    bad = httpx.MockTransport(lambda _: httpx.Response(200, json={'project_id': _PROJECT}))
    client = Client(api_key='test', transport=bad)
    with pytest.raises(VaultSetupError, match='identity'):
        asyncio.run(client._thread_private_data('thread', {'name': 'Ada'}))
    assert native.opens == 0


def test_provider_failure_is_sanitized(native: type[NativeVault], tmp_path: Path) -> None:
    """Provider details and secrets never enter the actionable SDK error."""
    _ = native

    def unavailable() -> bytes:
        message = 'sensitive-provider-response'
        raise RuntimeError(message)

    client = Client(
        api_key='test',
        transport=transport(),
        local_vault=LocalVaultConfig(
            directory=tmp_path,
            key_provider=unavailable,
        ),
    )
    with pytest.raises(VaultSetupError) as error:
        asyncio.run(client._thread_private_data('thread', {'name': 'Ada'}))
    assert 'sensitive-provider-response' not in str(error.value)
    assert error.value.__suppress_context__


def test_server_key_requires_deliberate_durable_directory() -> None:
    """Server configuration must not silently choose an ephemeral directory."""
    with pytest.raises(ValueError, match='directory'):
        LocalVaultConfig(key_provider=lambda: b'x' * 32)


def test_control_origin_isolates_storage(native: type[NativeVault]) -> None:
    """Identical ids from another API environment cannot reopen local records."""
    _ = native

    async def run() -> None:
        first = Client(api_key='a', base_url='https://one.example', transport=transport())
        await first._thread_private_data('thread', {'name': 'Ada'})
        second = Client(api_key='a', base_url='https://two.example', transport=transport())
        assert await second._thread_private_data('thread', None) is None

    asyncio.run(run())


def test_automatic_store_can_be_forgotten(native: type[NativeVault]) -> None:
    """Default storage has a public deletion path, including after restart."""
    _ = native

    async def run() -> None:
        first = Client(api_key='a', transport=transport())
        await first._thread_private_data('thread', {'name': 'Ada'})
        second = Client(api_key='rotated', transport=transport())
        await second.aforget_private_data('thread')
        await second.aforget_private_data('thread')
        assert await first._thread_private_data('thread', None) is None

    asyncio.run(run())


def test_key_provider_requires_exact_secret_size(native: type[NativeVault], tmp_path: Path) -> None:
    """Invalid configured key material never reaches native opening."""
    client = Client(
        api_key='a',
        transport=transport(),
        local_vault=LocalVaultConfig(directory=tmp_path, key_provider=lambda: b'short'),
    )
    with pytest.raises(VaultSetupError):
        asyncio.run(client._thread_private_data('thread', {'name': 'Ada'}))
    assert native.opens == 0


def test_hosting_environment_requires_no_extra_client_code(
    native: type[NativeVault],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Hosting-provided secrets and a durable path configure ordinary Client use."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('MAIVN_VAULT_KEY', base64.b64encode(b'x' * 32).decode())
    client = Client(api_key='a', transport=transport())
    asyncio.run(client._thread_private_data('thread', {'name': 'Ada'}))
    assert client._automatic_vault is not None
    config = client._automatic_vault._config
    assert config is not None
    assert str(config.directory) == str(tmp_path)
    assert config.key_provider is not None
    assert config.key_provider() == b'x' * 32
    assert 'eHh4' not in repr(config)
    assert native.opens == 1


def test_invalid_hosting_key_never_leaks(
    native: type[NativeVault],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Misconfigured hosting secrets produce value-free guidance."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('MAIVN_VAULT_KEY', 'sensitive-invalid-base64')
    client = Client(api_key='a', transport=transport())
    with pytest.raises(VaultSetupError) as error:
        asyncio.run(client._thread_private_data('thread', {'name': 'Ada'}))
    assert 'sensitive-invalid-base64' not in str(error.value)
    assert native.opens == 0


def test_storage_failure_after_open_is_sanitized(
    native: type[NativeVault],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Later read failures receive the same value-free boundary as setup."""
    client = Client(api_key='a', transport=transport())
    asyncio.run(client._thread_private_data('thread', {'name': 'Ada'}))

    def damaged(*_args: object) -> bytes:
        message = 'sensitive-native-path'
        raise RuntimeError(message)

    monkeypatch.setattr(native, 'load_value_map', damaged)
    with pytest.raises(VaultSetupError) as error:
        asyncio.run(client._thread_private_data('thread', None))
    assert 'sensitive-native-path' not in str(error.value)


def test_unused_broken_hosting_config_does_not_disable_ordinary_calls(
    native: type[NativeVault],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unused storage configuration does not block a call without private values."""
    monkeypatch.setenv('MAIVN_VAULT_KEY', 'incomplete-hosting-config')
    monkeypatch.delenv('MAIVN_VAULT_DIRECTORY', raising=False)
    client = Client(api_key='a')
    assert asyncio.run(client._thread_private_data('thread', None)) is None
    assert native.opens == 0


def test_base_path_separates_environments_with_identical_ids(native: type[NativeVault]) -> None:
    """Different mounted backends on one host must not share private records."""
    _ = native
    first = automatic_vault._scope(
        'https://api.example/one',
        {
            'user_id': _USER,
            'org_id': _ORG,
            'project_id': _PROJECT,
        },
    )
    second = automatic_vault._scope(
        'https://api.example/two',
        {
            'user_id': _USER,
            'org_id': _ORG,
            'project_id': _PROJECT,
        },
    )
    assert first != second
