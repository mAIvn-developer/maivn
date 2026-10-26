"""Characterize the caller-side private-data stores and their native vault boundary."""

from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from maivn import EncryptedFilePrivateDataStore, InMemoryPrivateDataStore
from maivn._internal.private_data_store import merged_private_data, persistable_pairs

if TYPE_CHECKING:
    from pathlib import Path

_MATERIAL = b'k' * 32
_MISSING_WHEEL = (
    'EncryptedFilePrivateDataStore needs the private_data_vault wheel '
    '(built from libraries/maivn-private-data-vault). Install it into '
    'this environment, or use InMemoryPrivateDataStore for a '
    'non-durable store.'
)


class RecordMissingError(Exception):
    """Fake native missing-record error."""


class NativeFailureError(Exception):
    """Fake native failure that is not a missing record."""


class FakeLocalVault:
    """Record every native call the store makes."""

    calls: list[tuple[object, ...]]
    records: dict[tuple[str, str], bytes]
    failure: Exception | None

    def __init__(self, path: str, secret: bytes) -> None:
        """Remember the construction arguments."""
        self.calls = [('open', path, secret)]
        self.records = {}
        self.failure = None
        FakeLocalVault.last = self

    last: FakeLocalVault | None = None

    def merge_value_map(self, tenant: str, record: str, payload: bytes) -> None:
        """Store the exact payload bytes."""
        self.calls.append(('merge', tenant, record, payload))
        self.records[tenant, record] = payload

    def load_value_map(self, tenant: str, record: str) -> bytes:
        """Return the stored bytes, a configured failure, or the missing-record error."""
        self.calls.append(('load', tenant, record))
        if self.failure is not None:
            raise self.failure
        try:
            return self.records[tenant, record]
        except KeyError:
            raise RecordMissingError from None

    def purge(self, tenant: str, record: str) -> None:
        """Delete one record."""
        self.calls.append(('purge', tenant, record))
        self.records.pop((tenant, record), None)


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> type[FakeLocalVault]:
    """Install a fake native wheel so no real key store or disk is touched."""
    module = ModuleType('private_data_vault')
    module.LocalVault = FakeLocalVault  # pyright: ignore[reportAttributeAccessIssue]
    module.RecordNotFound = RecordMissingError  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setitem(sys.modules, 'private_data_vault', module)
    FakeLocalVault.last = None
    return FakeLocalVault


def _opened(native: type[FakeLocalVault]) -> FakeLocalVault:
    vault = native.last
    assert vault is not None
    return vault


def test_in_memory_store_merges_resolves_subsets_and_forgets() -> None:
    """Pairs merge per thread, subsets omit absent keys, and forgetting is idempotent."""
    store = InMemoryPrivateDataStore()

    async def exercise() -> None:
        await store.remember('t', {})
        assert await store.resolve('t', ()) == {}
        await store.remember('t', {'a': '1', 'b': '2'})
        await store.remember('t', {'b': '3'})
        assert await store.resolve('t', ()) == {'a': '1', 'b': '3'}
        assert await store.resolve('t', ['b', 'missing']) == {'b': '3'}
        assert await store.resolve('other', ()) == {}
        whole = await store.resolve('t', ())
        whole['a'] = 'tampered'
        assert await store.resolve('t', ['a']) == {'a': '1'}
        await store.forget('t')
        await store.forget('t')
        assert await store.resolve('t', ()) == {}

    asyncio.run(exercise())


def test_encrypted_store_seals_exact_ascii_payloads_under_one_tenant(
    native: type[FakeLocalVault], tmp_path: Path
) -> None:
    """The native vault receives sorted ASCII-escaped JSON under the fixed sdk tenant."""
    store = EncryptedFilePrivateDataStore(str(tmp_path), _MATERIAL)
    vault = _opened(native)

    async def exercise() -> None:
        await store.remember('thread-1', {})
        await store.remember('thread-1', {'zeta': 'Zoë', 'alpha': 'A'})

    asyncio.run(exercise())
    assert vault.calls == [
        ('open', str(tmp_path), _MATERIAL),
        ('merge', 'sdk', 'thread-1', b'{"alpha": "A", "zeta": "Zo\\u00eb"}'),
    ]


def test_encrypted_store_resolves_only_string_values(
    native: type[FakeLocalVault], tmp_path: Path
) -> None:
    """Stored non-string values never surface and subsets omit absent keys."""
    store = EncryptedFilePrivateDataStore(str(tmp_path), _MATERIAL)
    vault = _opened(native)
    vault.records['sdk', 'thread-1'] = json.dumps({'a': 'x', 'n': 5, 'b': 'y', 'z': None}).encode()

    async def exercise() -> None:
        assert await store.resolve('thread-1', ()) == {'a': 'x', 'b': 'y'}
        assert await store.resolve('thread-1', ['b', 'n', 'gone']) == {'b': 'y'}

    asyncio.run(exercise())


def test_encrypted_store_missing_record_is_empty_and_other_errors_propagate(
    native: type[FakeLocalVault], tmp_path: Path
) -> None:
    """Only the native missing-record error means empty; every other failure surfaces."""
    store = EncryptedFilePrivateDataStore(str(tmp_path), _MATERIAL)
    vault = _opened(native)
    assert asyncio.run(store.resolve('absent', ())) == {}
    vault.failure = NativeFailureError('authentication failed')
    with pytest.raises(NativeFailureError):
        asyncio.run(store.resolve('absent', ()))


def test_encrypted_store_forget_purges_the_thread_record(
    native: type[FakeLocalVault], tmp_path: Path
) -> None:
    """Forgetting purges exactly the thread's record under the sdk tenant."""
    store = EncryptedFilePrivateDataStore(str(tmp_path), _MATERIAL)
    vault = _opened(native)
    asyncio.run(store.forget('thread-9'))
    assert vault.calls[-1] == ('purge', 'sdk', 'thread-9')


def test_from_native_wraps_an_opened_vault_without_reopening(
    native: type[FakeLocalVault], tmp_path: Path
) -> None:
    """An already-open native vault is used as is."""
    opened = native(str(tmp_path), _MATERIAL)
    store = EncryptedFilePrivateDataStore.from_native(opened)
    asyncio.run(store.remember('t', {'k': 'v'}))
    assert opened.calls == [
        ('open', str(tmp_path), _MATERIAL),
        ('merge', 'sdk', 't', b'{"k": "v"}'),
    ]


def test_missing_wheel_names_the_remedy_and_keeps_the_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the wheel the store explains how to install it or which store to use."""
    monkeypatch.setitem(sys.modules, 'private_data_vault', None)
    with pytest.raises(ModuleNotFoundError) as caught:
        EncryptedFilePrivateDataStore(str(tmp_path), _MATERIAL)
    assert str(caught.value) == _MISSING_WHEEL
    assert isinstance(caught.value.__cause__, ModuleNotFoundError)


def test_merged_private_data_lets_supplied_values_win() -> None:
    """Supplied values override stored ones, and stored values fill the rest."""
    assert merged_private_data({'a': '1', 'b': '2'}, {'b': 'new', 3: 4}) == {
        'a': '1',
        'b': 'new',
        3: 4,
    }
    assert merged_private_data({'a': '1'}, None) == {'a': '1'}
    assert merged_private_data({}, {}) == {}


def test_persistable_pairs_keep_only_string_pairs() -> None:
    """Only string-to-string pairs are eligible for storage."""
    assert persistable_pairs(None) == {}
    assert persistable_pairs({}) == {}
    assert persistable_pairs({'a': 'x', 'b': 1, 2: 'y', 'c': None, 'd': ''}) == {
        'a': 'x',
        'd': '',
    }
