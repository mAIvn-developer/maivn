"""Characterize the automatic local vault: identity scoping, custody checks, error hygiene."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, ClassVar

import httpx
import pytest

from maivn import LocalVaultConfig, VaultSetupError
from maivn._internal.automatic_vault import AutomaticVault
from maivn._internal.transport.http import HttpJsonClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_USER = '00000000-0000-0000-0000-00000000000A'
_ORG = '00000000-0000-0000-0000-00000000000B'
_PROJECT = '00000000-0000-0000-0000-00000000000C'
_SENTINEL = 'sentinel-private-native-detail'
_SETUP_HELP = (
    'Encrypted local storage is unavailable. Unlock your operating-system credential store. '
    'On servers, provide LocalVaultConfig(directory=..., key_provider=...) using a secret '
    'manager and durable storage. Serverless temporary files are not durable vault storage. '
    'Existing vault data and its key must be restored together; no fallback was used.'
)
_IDENTITY_HELP = (
    'Unable to verify vault account identity. Check your API key and network connection.'
)


class RecordMissingError(Exception):
    """Fake native missing-record error."""


class NativeVault:
    """One opened fake vault holding value maps."""

    def __init__(self, scope: str) -> None:
        """Bind the opened scope."""
        self.scope = scope
        self.records: dict[str, bytes] = {}
        self.purges: list[str] = []
        self.failure: Exception | None = None

    def merge_value_map(self, tenant: str, record: str, payload: bytes) -> None:
        """Merge a payload, or fail when configured."""
        assert tenant == 'sdk'
        if self.failure is not None:
            raise self.failure
        prior = json.loads(self.records.get(record, b'{}'))
        prior.update(json.loads(payload))
        self.records[record] = json.dumps(prior).encode()

    def load_value_map(self, tenant: str, record: str) -> bytes:
        """Load a payload or report a missing record."""
        assert tenant == 'sdk'
        try:
            return self.records[record]
        except KeyError:
            raise RecordMissingError from None

    def purge(self, tenant: str, record: str) -> None:
        """Purge a record, or fail when configured."""
        assert tenant == 'sdk'
        if self.failure is not None:
            raise self.failure
        self.purges.append(record)


@dataclass
class NativeState:
    """Observable state of the fake native wheel."""

    existing: set[str | None] = field(default_factory=set[str | None])
    exists_calls: list[tuple[str | None, str | None]] = field(
        default_factory=list[tuple[str | None, str | None]]
    )
    opens: list[tuple[str, str | None, bytes | None]] = field(
        default_factory=list[tuple[str, str | None, bytes | None]]
    )
    vaults: list[NativeVault] = field(default_factory=list[NativeVault])
    open_failure: Exception | None = None


class LocalVault:
    """Fake native LocalVault entry points."""

    state: ClassVar[NativeState] = NativeState()

    @classmethod
    def default_exists(cls, scope: str | None = None, *, path: str | None = None) -> bool:
        """Report whether the unscoped or scoped default vault exists."""
        cls.state.exists_calls.append((scope, path))
        return scope in cls.state.existing

    @classmethod
    def open_default(
        cls, scope: str, *, path: str | None = None, secret: bytes | None = None
    ) -> NativeVault:
        """Open the scope or fail when configured."""
        if cls.state.open_failure is not None:
            raise cls.state.open_failure
        cls.state.opens.append((scope, path, secret))
        cls.state.existing.update({None, scope})
        vault = NativeVault(scope)
        cls.state.vaults.append(vault)
        return vault


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> NativeState:
    """Replace the native wheel so no OS credential store or real disk is used."""
    module = ModuleType('private_data_vault')
    module.LocalVault = LocalVault  # pyright: ignore[reportAttributeAccessIssue]
    module.RecordNotFound = RecordMissingError  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, 'private_data_vault', module)
    LocalVault.state = NativeState()
    return LocalVault.state


@dataclass
class Identity:
    """An identity endpoint double that records requests."""

    base_url: str = 'https://api.example'
    body: dict[str, object] = field(
        default_factory=lambda: {'user_id': _USER, 'org_id': _ORG, 'project_id': _PROJECT}
    )
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    def factory(self) -> Callable[[], HttpJsonClient]:
        """Return an HTTP factory bound to this double."""

        def respond(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(200, json=self.body)

        def build() -> HttpJsonClient:
            return HttpJsonClient(
                base_url=self.base_url,
                api_key='identity-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(respond),
            )

        return build


def _scope(origin: str) -> str:
    ids = [_USER.lower(), _ORG.lower(), _PROJECT.lower()]
    payload = json.dumps(['maivn-local-vault-v1', origin, *ids], separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _setup_error(caught: pytest.ExceptionInfo[VaultSetupError], message: str) -> None:
    assert str(caught.value) == message
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert _SENTINEL not in str(caught.value)


def test_vault_config_validates_and_hides_the_key_provider() -> None:
    """Configuration refuses unsafe combinations and never shows the key callback."""
    with pytest.raises(ValueError, match=r'^The vault directory must not be empty\.$'):
        LocalVaultConfig(directory='  ')
    with pytest.raises(
        ValueError,
        match=r'^A server vault key provider requires an explicit durable directory\.$',
    ):
        LocalVaultConfig(key_provider=lambda: b'k' * 32)

    def provider() -> bytes:
        return b'k' * 32

    config = LocalVaultConfig(directory='/srv/vault', key_provider=provider)
    assert repr(config) == "LocalVaultConfig(directory='/srv/vault')"
    assert config == LocalVaultConfig(directory='/srv/vault')


def test_no_existing_vault_and_no_values_contacts_nothing(
    native: NativeState, tmp_path: Path
) -> None:
    """A call without values and without a vault neither verifies identity nor opens."""
    identity = Identity()
    vault = AutomaticVault(identity.factory(), LocalVaultConfig(directory=tmp_path))
    assert asyncio.run(vault.store(has_values=False)) is None
    assert native.exists_calls == [(None, str(tmp_path))]
    assert identity.requests == []
    assert native.opens == []


def test_values_open_a_scope_bound_to_origin_and_identity(
    native: NativeState, tmp_path: Path
) -> None:
    """New values verify identity with the API key and open the derived scope."""
    identity = Identity()
    vault = AutomaticVault(identity.factory(), LocalVaultConfig(directory=tmp_path))
    store = asyncio.run(vault.store(has_values=True))
    assert store is not None
    assert native.opens == [(_scope('https://api.example'), str(tmp_path), None)]
    assert [request.url.path for request in identity.requests] == ['/v1/vault/identity']
    assert identity.requests[0].headers['authorization'] == 'Bearer identity-key'
    assert asyncio.run(vault.store(has_values=False)) is store
    assert len(identity.requests) == 1


@pytest.mark.parametrize(
    ('base_url', 'origin'),
    [
        ('https://API.Example:443/', 'https://api.example'),
        ('http://api.example:80', 'http://api.example'),
        ('https://api.example:8443/base/', 'https://api.example:8443/base'),
        ('http://[::1]:8000', 'http://[::1]:8000'),
    ],
)
def test_scope_normalizes_the_api_origin(
    native: NativeState, tmp_path: Path, base_url: str, origin: str
) -> None:
    """Default ports, case and trailing slashes do not change the vault scope."""
    identity = Identity(base_url=base_url)
    vault = AutomaticVault(identity.factory(), LocalVaultConfig(directory=tmp_path))
    asyncio.run(vault.store(has_values=True))
    assert [scope for scope, _path, _secret in native.opens] == [_scope(origin)]


@pytest.mark.parametrize(
    'base_url',
    [
        'https://user@api.example',
        'https://:pw@api.example',
        'https://api.example/?tenant=other',
        'https://api.example/#other',
        'ftp://api.example',
        'https:///no-host',
    ],
)
def test_ambiguous_origins_cannot_select_storage(
    native: NativeState, tmp_path: Path, base_url: str
) -> None:
    """Credentials, queries, fragments, other schemes or no host refuse after identity."""
    identity = Identity(base_url=base_url)
    vault = AutomaticVault(identity.factory(), LocalVaultConfig(directory=tmp_path))
    with pytest.raises(VaultSetupError) as caught:
        asyncio.run(vault.store(has_values=True))
    _setup_error(caught, _IDENTITY_HELP)
    assert len(identity.requests) == 1
    assert native.opens == []


@pytest.mark.parametrize(
    'body',
    [
        {'user_id': _USER, 'org_id': _ORG},
        {'user_id': 'not-a-uuid', 'org_id': _ORG, 'project_id': _PROJECT},
        {'user_id': _USER, 'org_id': _ORG, 'project_id': 7},
    ],
)
def test_incomplete_identity_cannot_select_storage(
    native: NativeState, tmp_path: Path, body: dict[str, object]
) -> None:
    """Every identity component must be a UUID before a scope exists."""
    identity = Identity(body=body)
    vault = AutomaticVault(identity.factory(), LocalVaultConfig(directory=tmp_path))
    with pytest.raises(VaultSetupError) as caught:
        asyncio.run(vault.store(has_values=True))
    _setup_error(caught, _IDENTITY_HELP)
    assert native.opens == []


def test_existing_vault_for_another_scope_is_not_opened(
    native: NativeState, tmp_path: Path
) -> None:
    """An existing vault for a different identity yields nothing without new values."""
    native.existing.update({None, 'other-scope'})
    identity = Identity()
    vault = AutomaticVault(identity.factory(), LocalVaultConfig(directory=tmp_path))
    assert asyncio.run(vault.store(has_values=False)) is None
    assert native.exists_calls == [
        (None, str(tmp_path)),
        (_scope('https://api.example'), str(tmp_path)),
    ]
    assert native.opens == []


def test_missing_native_wheel_is_a_setup_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unavailable native wheel yields fixed guidance without import details."""
    monkeypatch.setitem(sys.modules, 'private_data_vault', None)
    vault = AutomaticVault(Identity().factory(), LocalVaultConfig(directory=tmp_path))
    with pytest.raises(VaultSetupError) as caught:
        asyncio.run(vault.store(has_values=True))
    _setup_error(caught, _SETUP_HELP)


def test_native_open_failure_is_sanitized(native: NativeState, tmp_path: Path) -> None:
    """Native open errors never leak their text into the SDK error."""
    native.open_failure = RuntimeError(_SENTINEL)
    vault = AutomaticVault(Identity().factory(), LocalVaultConfig(directory=tmp_path))
    with pytest.raises(VaultSetupError) as caught:
        asyncio.run(vault.store(has_values=True))
    _setup_error(caught, _SETUP_HELP)


@pytest.mark.parametrize('material', [b'k' * 31, b'k' * 33, bytearray(b'k' * 32), 'k' * 32])
def test_key_provider_must_return_exactly_32_bytes(
    native: NativeState, tmp_path: Path, material: object
) -> None:
    """Only a bytes object of exactly 32 bytes may reach the native vault."""

    def provider() -> bytes:
        return material  # type: ignore[return-value] - deliberately wrong provider output.

    vault = AutomaticVault(
        Identity().factory(), LocalVaultConfig(directory=tmp_path, key_provider=provider)
    )
    with pytest.raises(VaultSetupError) as caught:
        asyncio.run(vault.store(has_values=True))
    _setup_error(caught, _SETUP_HELP)
    assert native.opens == []


def test_valid_key_provider_secret_reaches_the_native_vault(
    native: NativeState, tmp_path: Path
) -> None:
    """An exact 32-byte provider secret is passed to the native open."""
    vault = AutomaticVault(
        Identity().factory(),
        LocalVaultConfig(directory=tmp_path, key_provider=lambda: b's' * 32),
    )
    asyncio.run(vault.store(has_values=True))
    assert [secret for _scope_value, _path, secret in native.opens] == [b's' * 32]


def test_environment_configuration_is_read_lazily(
    native: NativeState, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without explicit config the hosting directory and key come from the environment."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    material = bytes(range(100, 132))
    monkeypatch.setenv('MAIVN_VAULT_KEY', base64.b64encode(material).decode())
    vault = AutomaticVault(Identity().factory(), None)
    asyncio.run(vault.store(has_values=True))
    assert native.exists_calls == []
    assert [(path, secret) for _scope_value, path, secret in native.opens] == [
        (str(tmp_path), material)
    ]


def test_merge_persists_string_pairs_and_lets_the_caller_win(
    native: NativeState, tmp_path: Path
) -> None:
    """Supplied string pairs are remembered; later calls receive them unless overridden."""
    vault = AutomaticVault(Identity().factory(), LocalVaultConfig(directory=tmp_path))

    async def exercise() -> None:
        first = await vault.merge('thread', {'name': 'Ada', 'count': 3})
        assert first == {'name': 'Ada', 'count': 3}
        assert await vault.merge('thread', None) == {'name': 'Ada'}
        assert await vault.merge('thread', {'name': 'Grace'}) == {'name': 'Grace'}
        assert await vault.merge('other-thread', None) is None

    asyncio.run(exercise())
    assert json.loads(native.vaults[0].records['thread']) == {'name': 'Grace'}


def test_merge_without_storage_returns_the_supplied_mapping(
    native: NativeState, tmp_path: Path
) -> None:
    """Values that cannot be persisted pass through untouched without opening storage."""
    supplied: dict[object, object] = {'count': 3}
    vault = AutomaticVault(Identity().factory(), LocalVaultConfig(directory=tmp_path))
    assert asyncio.run(vault.merge('thread', supplied)) is supplied
    assert native.opens == []


def test_merge_and_forget_failures_are_sanitized(native: NativeState, tmp_path: Path) -> None:
    """Native failures after opening become the fixed setup guidance."""
    vault = AutomaticVault(Identity().factory(), LocalVaultConfig(directory=tmp_path))
    asyncio.run(vault.merge('thread', {'name': 'Ada'}))
    native.vaults[0].failure = RuntimeError(_SENTINEL)
    with pytest.raises(VaultSetupError) as caught:
        asyncio.run(vault.merge('thread', {'name': 'Grace'}))
    _setup_error(caught, _SETUP_HELP)
    with pytest.raises(VaultSetupError) as forgotten:
        asyncio.run(vault.forget('thread'))
    _setup_error(forgotten, _SETUP_HELP)


def test_forget_purges_an_existing_store_and_never_creates_one(
    native: NativeState, tmp_path: Path
) -> None:
    """Forgetting without a vault is a no-op; with one it purges the thread."""
    vault = AutomaticVault(Identity().factory(), LocalVaultConfig(directory=tmp_path))
    asyncio.run(vault.forget('thread'))
    assert native.opens == []
    asyncio.run(vault.merge('thread', {'name': 'Ada'}))
    asyncio.run(vault.forget('thread'))
    assert native.vaults[0].purges == ['thread']
