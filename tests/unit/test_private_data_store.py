"""Unit tests for SDK-resident private_data persistence."""

from __future__ import annotations

import asyncio
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from maivn import EncryptedFilePrivateDataStore, InMemoryPrivateDataStore, PrivateDataStore
from maivn._internal.client import Client
from maivn._internal.private_data_store import merged_private_data, persistable_pairs

_VAULT_WHEEL_PRESENT = importlib.util.find_spec('private_data_vault') is not None
_SECRET = b'0' * 32


def test_in_memory_store_remembers_resolves_and_forgets() -> None:
    """The process-lifetime store keeps a thread's map until told to forget."""

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        await store.remember('thread-1', {'pii_person_1': 'Dana Whitfield'})
        await store.remember('thread-1', {'pii_ssn_1': '123-45-6789'})

        everything = await store.resolve('thread-1', ())
        assert everything == {
            'pii_person_1': 'Dana Whitfield',
            'pii_ssn_1': '123-45-6789',
        }
        subset = await store.resolve('thread-1', ('pii_ssn_1', 'pii_missing'))
        assert subset == {'pii_ssn_1': '123-45-6789'}

        await store.forget('thread-1')
        assert await store.resolve('thread-1', ()) == {}
        # Forgetting again is idempotent.
        await store.forget('thread-1')

    asyncio.run(exercise())


def test_in_memory_store_isolates_threads() -> None:
    """One conversation's map never resolves into another conversation."""

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        await store.remember('thread-a', {'pii_person_1': 'Dana Whitfield'})

        assert await store.resolve('thread-b', ()) == {}

    asyncio.run(exercise())


def test_stores_satisfy_the_protocol() -> None:
    """Both sanctioned adapters are structural PrivateDataStore implementations."""
    assert isinstance(InMemoryPrivateDataStore(), PrivateDataStore)


def test_encrypted_store_requires_an_atomic_merge_capable_wheel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An outdated optional backend fails at construction with a precise upgrade remedy."""
    monkeypatch.setattr(
        'maivn._internal.private_data_store._vault_module',
        lambda: SimpleNamespace(LocalVault=object),
    )
    with pytest.raises(TypeError, match='atomic merge_value_map support'):
        EncryptedFilePrivateDataStore(str(tmp_path), _SECRET)


@pytest.mark.skipif(not _VAULT_WHEEL_PRESENT, reason='private_data_vault wheel not installed')
def test_encrypted_file_store_persists_across_reopen(tmp_path: object) -> None:
    """A sealed map survives process death: a new store over the same path reads it."""

    async def exercise() -> None:
        path = str(tmp_path)
        first = EncryptedFilePrivateDataStore(path, _SECRET)
        await first.remember('thread-1', {'pii_person_1': 'Dana Whitfield'})

        # A fresh store instance models a restarted customer process.
        second = EncryptedFilePrivateDataStore(path, _SECRET)
        assert isinstance(second, PrivateDataStore)
        resolved = await second.resolve('thread-1', ('pii_person_1',))
        assert resolved == {'pii_person_1': 'Dana Whitfield'}

        await second.forget('thread-1')
        third = EncryptedFilePrivateDataStore(path, _SECRET)
        assert await third.resolve('thread-1', ()) == {}

    asyncio.run(exercise())


@pytest.mark.skipif(not _VAULT_WHEEL_PRESENT, reason='private_data_vault wheel not installed')
def test_encrypted_file_store_writes_no_plaintext(tmp_path: object) -> None:
    """Nothing under the store's directory contains the raw value in the clear."""

    async def exercise() -> None:
        store = EncryptedFilePrivateDataStore(str(tmp_path), _SECRET)
        await store.remember('thread-1', {'pii_person_1': 'Dana Whitfield'})

    asyncio.run(exercise())
    for file in Path(str(tmp_path)).rglob('*'):
        if file.is_file():
            assert b'Dana Whitfield' not in file.read_bytes()


@pytest.mark.skipif(not _VAULT_WHEEL_PRESENT, reason='private_data_vault wheel not installed')
def test_encrypted_file_store_refuses_the_wrong_secret(tmp_path: object) -> None:
    """A different key opens nothing rather than something."""

    async def exercise() -> None:
        path = str(tmp_path)
        store = EncryptedFilePrivateDataStore(path, _SECRET)
        await store.remember('thread-1', {'pii_person_1': 'Dana Whitfield'})

        intruder = EncryptedFilePrivateDataStore(path, b'1' * 32)
        with pytest.raises(Exception, match=r'(?i)authenticat'):
            await intruder.resolve('thread-1', ())

    asyncio.run(exercise())


@pytest.mark.skipif(not _VAULT_WHEEL_PRESENT, reason='private_data_vault wheel not installed')
def test_encrypted_remember_preserves_concurrent_keys_across_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent adapters must merge under the shared native storage transaction."""
    first = EncryptedFilePrivateDataStore(str(tmp_path), _SECRET)
    second = EncryptedFilePrivateDataStore(str(tmp_path), _SECRET)
    barrier = threading.Barrier(2)
    original_load = EncryptedFilePrivateDataStore._load_sync  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    original_remember = EncryptedFilePrivateDataStore._remember_sync  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    start = threading.Barrier(2)

    def synchronized_remember(
        self: EncryptedFilePrivateDataStore, thread_id: str, mapping: dict[str, str]
    ) -> None:
        start.wait(timeout=5)
        original_remember(self, thread_id, mapping)

    def synchronized_load(self: EncryptedFilePrivateDataStore, thread_id: str) -> dict[str, str]:
        loaded = original_load(self, thread_id)
        barrier.wait(timeout=5)
        return loaded

    async def exercise() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr(EncryptedFilePrivateDataStore, '_load_sync', synchronized_load)
            patcher.setattr(EncryptedFilePrivateDataStore, '_remember_sync', synchronized_remember)
            await asyncio.gather(
                first.remember('thread-1', {'first': 'one'}),
                second.remember('thread-1', {'second': 'two'}),
            )
        assert await first.resolve('thread-1', ()) == {'first': 'one', 'second': 'two'}

    asyncio.run(exercise())


def test_merged_private_data_lets_the_caller_win() -> None:
    """A per-invoke value overrides the persisted one for that turn only."""
    merged = merged_private_data(
        {'pii_person_1': 'Dana Whitfield', 'pii_ssn_1': '123-45-6789'},
        {'pii_person_1': 'Redacted For This Turn'},
    )
    assert merged == {
        'pii_person_1': 'Redacted For This Turn',
        'pii_ssn_1': '123-45-6789',
    }


def test_persistable_pairs_keeps_only_string_pairs() -> None:
    """Non-string per-invoke structure passes through calls but never rests."""
    pairs = persistable_pairs(
        {'pii_person_1': 'Dana Whitfield', 'nested': {'x': 1}, 7: 'seven'},
    )
    assert pairs == {'pii_person_1': 'Dana Whitfield'}
    assert persistable_pairs(None) == {}


def test_client_without_a_store_passes_private_data_through_unchanged() -> None:
    """No store configured means exactly the pre-store behavior."""

    async def exercise() -> None:
        client = Client(
            api_key='mvn_test_key',
            base_url='http://127.0.0.1:1',
            private_data_store=None,
        )
        supplied: dict[object, object] = {'pii_person_1': 'Dana Whitfield'}

        # The client's private-data merge is the unit under test.
        merged = await client._thread_private_data('thread-1', supplied)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]

        assert merged is supplied

    asyncio.run(exercise())


def test_client_with_a_store_resolves_earlier_turns_into_the_call() -> None:
    """Turn 1 supplies the map; turn 3 resolves it without re-supplying."""

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        client = Client(
            api_key='mvn_test_key',
            base_url='http://127.0.0.1:1',
            private_data_store=store,
        )

        # The client's private-data merge is the unit under test.
        turn_one = await client._thread_private_data(  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            'thread-1',
            {'pii_person_1': 'Dana Whitfield'},
        )
        assert turn_one == {'pii_person_1': 'Dana Whitfield'}

        # Later turn, nothing supplied: the store fills the map back in.
        turn_three = await client._thread_private_data('thread-1', None)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        assert turn_three == {'pii_person_1': 'Dana Whitfield'}

        # Without a thread there is no scope key, so nothing resolves.
        no_thread = await client._thread_private_data(None, None)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        assert no_thread is None

    asyncio.run(exercise())
