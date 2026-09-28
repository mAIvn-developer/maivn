"""Lazy native storage scoped by authenticated identity rather than API keys."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from maivn._internal.errors import ConfigurationError
from maivn._internal.private_data_store import (
    EncryptedFilePrivateDataStore,
    merged_private_data,
    persistable_pairs,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from maivn._internal.transport.http import HttpJsonClient


class VaultSetupError(ConfigurationError):
    """Private storage could not open without weakening its custody policy."""


@dataclass(frozen=True)
class LocalVaultConfig:
    """Optional location and a server secret-store callback.

    The provider returns 32 secret bytes and requires a durable directory.
    It runs only when private storage is used.
    """

    directory: str | Path | None = None
    key_provider: Callable[[], bytes] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Reject incomplete server configuration before side effects."""
        if self.key_provider is not None and self.directory is None:
            message = 'A server vault key provider requires an explicit durable directory.'
            raise ValueError(message)
        if self.directory is not None and not str(self.directory).strip():
            message = 'The vault directory must not be empty.'
            raise ValueError(message)


class AutoVault:
    """Distinguish an omitted store from explicit no-persistence."""


AUTO_VAULT = AutoVault()
_KEY_BYTES = 32


def _native() -> Any:
    return import_module('private_data_vault')


class AutomaticVault:
    """Resolve stable authority and open a native store when needed."""

    def __init__(self, http: Callable[[], HttpJsonClient], config: LocalVaultConfig | None) -> None:
        """Keep only configuration until a thread needs persistence."""
        self._http = http
        self._config = config
        self._store: EncryptedFilePrivateDataStore | None = None

    async def store(self, *, has_values: bool) -> EncryptedFilePrivateDataStore | None:
        """Open existing storage, or create it for newly supplied private values."""
        if self._store is not None:
            return self._store
        from maivn._internal.config import (  # noqa: PLC0415 - lazy config boundary.
            local_vault_directory_from_environment,
            local_vault_from_environment,
        )

        directory = (
            self._config.directory
            if self._config is not None
            else local_vault_directory_from_environment()
        )
        path = None if directory is None else str(directory)
        try:
            native = _native().LocalVault
            if not has_values and not await asyncio.to_thread(native.default_exists, path=path):
                return None
        except Exception:  # noqa: BLE001 - sanitize untrusted provider/native errors.
            raise VaultSetupError(_SETUP_HELP) from None
        if self._config is None:
            self._config = local_vault_from_environment()
        try:
            http = self._http()
            identity = await http.get('/v1/vault/identity')
            scope = _scope(http.base_url, identity)
        except Exception:  # noqa: BLE001 - sanitize untrusted provider/native errors.
            message = (
                'Unable to verify vault account identity. '
                'Check your API key and network connection.'
            )
            raise VaultSetupError(message) from None
        try:
            if not has_values and not await asyncio.to_thread(
                native.default_exists, scope, path=path
            ):
                return None
            vault = await asyncio.to_thread(self._open, native, scope, path)
            store = EncryptedFilePrivateDataStore.from_native(vault)
        except Exception:  # noqa: BLE001 - sanitize untrusted provider/native errors.
            raise VaultSetupError(_SETUP_HELP) from None
        self._store = store
        return store

    async def merge(
        self,
        thread_id: str,
        supplied: Mapping[object, object] | None,
    ) -> Mapping[object, object] | None:
        """Remember caller values and resolve the thread without exposing native errors."""
        pairs = persistable_pairs(supplied)
        store = await self.store(has_values=bool(pairs))
        if store is None:
            return supplied
        try:
            if pairs:
                await store.remember(thread_id, pairs)
            stored = await store.resolve(thread_id, ())
        except Exception:  # noqa: BLE001 - native diagnostics are not public SDK errors.
            raise VaultSetupError(_SETUP_HELP) from None
        return merged_private_data(stored, supplied) if stored or supplied else supplied

    async def forget(self, thread_id: str) -> None:
        """Purge local values without creating a new vault or leaking native errors."""
        store = await self.store(has_values=False)
        if store is not None:
            try:
                await store.forget(thread_id)
            except Exception:  # noqa: BLE001 - native diagnostics are not public SDK errors.
                raise VaultSetupError(_SETUP_HELP) from None

    def _open(self, native: Any, scope: str, path: str | None) -> Any:
        secret = None
        if self._config is not None and self._config.key_provider is not None:
            secret = self._config.key_provider()
            if type(secret) is not bytes or len(secret) != _KEY_BYTES:
                message = 'The vault key provider must return 32 secret bytes.'
                raise ValueError(message)
        return native.open_default(scope, path=path, secret=secret)


def _scope(base_url: str, identity: dict[str, Any]) -> str:
    origin = urlsplit(base_url)
    if (
        origin.username is not None
        or origin.password is not None
        or origin.query
        or origin.fragment
    ):
        message = 'Invalid API origin.'
        raise ValueError(message)
    authority = origin.hostname
    if not authority or origin.scheme not in {'http', 'https'}:
        message = 'Invalid API origin.'
        raise ValueError(message)
    if ':' in authority:
        authority = '[' + authority + ']'
    if origin.port and (origin.scheme, origin.port) not in {('https', 443), ('http', 80)}:
        authority += ':' + str(origin.port)
    normalized = urlunsplit((origin.scheme, authority, origin.path.rstrip('/'), '', ''))
    ids = [str(UUID(cast('str', identity[name]))) for name in ('user_id', 'org_id', 'project_id')]
    payload = json.dumps(['maivn-local-vault-v1', normalized, *ids], separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


_SETUP_HELP = (
    'Encrypted local storage is unavailable. Unlock your operating-system credential store. '
    'On servers, provide LocalVaultConfig(directory=..., key_provider=...) using a secret '
    'manager and durable storage. Serverless temporary files are not durable vault storage. '
    'Existing vault data and its key must be restored together; no fallback was used.'
)
