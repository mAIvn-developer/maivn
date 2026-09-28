"""Customer-side persistence for private_data placeholder maps.

``private_data`` is the placeholder-to-value map that lets a redacted
conversation be rehydrated. Without a store it is ephemeral per invoke: the
caller must re-supply it on every turn, and where it rests between turns is
left to whatever the caller invents -- often a plaintext file next to the code.

This module gives that map a sanctioned resting place on the customer's side:

* :class:`PrivateDataStore` -- the small protocol the client accepts;
* :class:`InMemoryPrivateDataStore` -- process-lifetime only, nothing at rest;
* :class:`EncryptedFilePrivateDataStore` -- durable on the customer's disk,
  sealed by the same vault core the platform ships (XChaCha20-Poly1305);
  the sealing key is the customer's and never leaves their machine.

The client opens an encrypted store automatically when private thread values
need persistence. Explicit ``private_data_store=None`` keeps values within the
call that supplied them.

Two maps, one conversation
--------------------------

Since the 2026-08-21 custody ruling a conversation has TWO placeholder maps, and
knowing which is which is the whole of understanding what gets re-sent:

* **this store, on your side**, holds the values YOU declared, per thread. It
  exists so a long conversation does not have to re-supply the same
  ``serial_number`` on every turn;
* **server custody**, on ours, holds the complete map for the thread -- your
  declarations AND the values the shield discovered mid-run, which are keyed by
  names like ``pii_email_1`` that were allocated during a run you never saw the
  inside of. This is what makes turn two of a conversation able to resolve
  something turn one found.

Nothing the server allocates ever enters this store. The client is never handed
the server's map, so it cannot learn ``pii_email_1`` even in principle -- which
is exactly why server custody has to exist rather than being folded into this.

Precedence, when both hold a key:

* a name YOU chose (``serial_number``): what you pass on this call wins, both
  here and on the server. Re-declaring your own field is you saying what it
  means from this turn on;
* a name the SERVER allocated (``pii_email_1``): custody wins. That key already
  stands for one particular value in the stored transcript, and redefining it
  from a later request would change who the earlier turns were about;
* a value only one side holds is simply used.

What is deliberately not automatic: once a thread's pairs are remembered, they
are re-sent on every later call on that thread, including calls where you pass
no ``private_data`` at all. That is the point of the store, and
:meth:`PrivateDataStore.forget` is how you end it for a thread.
"""

from __future__ import annotations

import asyncio
import json
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# One customer process is one vault tenant; threads are records within it.
# The vault's tenant separation exists for the server's multi-tenant case --
# here the whole store belongs to one customer, so a constant is honest.
_TENANT = 'sdk'


@runtime_checkable
class PrivateDataStore(Protocol):
    """Where a conversation thread's placeholder map comes to rest.

    Holds the values the CALLER declared, and only those. Server-allocated keys
    live in server custody and never arrive here; see the module docstring for
    the division and the precedence rule.
    """

    async def remember(self, thread_id: str, mapping: Mapping[str, str]) -> None:
        """Merge placeholder-to-value pairs into the thread's persisted map.

        Merged, not replaced: turn five adding a field must not drop what turn
        one declared. A key supplied again takes the new value.
        """
        ...

    async def resolve(self, thread_id: str, placeholders: Sequence[str]) -> dict[str, str]:
        """Return the persisted values for these placeholders, absent ones omitted.

        An empty ``placeholders`` sequence asks for the whole map.
        """
        ...

    async def forget(self, thread_id: str) -> None:
        """Hard-delete the thread's persisted map. Idempotent."""
        ...


class InMemoryPrivateDataStore:
    """Process-lifetime store: nothing at rest, nothing to leak, gone on exit.

    The honest default for scripts and tests. Not durable across restarts,
    and does not pretend to be.
    """

    def __init__(self) -> None:
        """Create an empty in-process store."""
        self._threads: dict[str, dict[str, str]] = {}

    async def remember(self, thread_id: str, mapping: Mapping[str, str]) -> None:
        """Merge pairs into the thread's map."""
        if not mapping:
            return
        self._threads.setdefault(thread_id, {}).update(mapping)

    async def resolve(self, thread_id: str, placeholders: Sequence[str]) -> dict[str, str]:
        """Return persisted values for the placeholders, or the whole map."""
        stored = self._threads.get(thread_id, {})
        if not placeholders:
            return dict(stored)
        return {key: stored[key] for key in placeholders if key in stored}

    async def forget(self, thread_id: str) -> None:
        """Drop the thread's map."""
        self._threads.pop(thread_id, None)


class EncryptedFilePrivateDataStore:
    """Durable store sealed with the customer's own key, on the customer's disk.

    Reuses the platform's vault core (the ``private_data_vault`` wheel) rather
    than inventing a second cryptography surface: this store is ``LocalVault``
    pointed at a customer-chosen directory, with each conversation thread kept
    as one sealed value-map record. The wheel is included with the SDK.
    """

    def __init__(self, path: str, secret: bytes) -> None:
        """Open or create a sealed store rooted at ``path`` with the customer's secret."""
        self._vault = _local_vault(path, secret)
        self._errors = _vault_errors()

    @classmethod
    def from_native(cls, vault: Any) -> EncryptedFilePrivateDataStore:
        """Wrap a native vault without exposing its master key."""
        store = cls.__new__(cls)
        store._vault = vault  # noqa: SLF001 - alternate constructor.
        store._errors = _vault_errors()  # noqa: SLF001 - alternate constructor.
        return store

    async def remember(self, thread_id: str, mapping: Mapping[str, str]) -> None:
        """Merge pairs into the thread's sealed map."""
        if not mapping:
            return
        await asyncio.to_thread(self._remember_sync, thread_id, dict(mapping))

    async def resolve(self, thread_id: str, placeholders: Sequence[str]) -> dict[str, str]:
        """Return persisted values for the placeholders, or the whole map."""
        stored = await asyncio.to_thread(self._load_sync, thread_id)
        if not placeholders:
            return stored
        return {key: stored[key] for key in placeholders if key in stored}

    async def forget(self, thread_id: str) -> None:
        """Hard-delete the thread's sealed record. Idempotent."""
        await asyncio.to_thread(self._vault.purge, _TENANT, thread_id)

    def _remember_sync(self, thread_id: str, mapping: dict[str, str]) -> None:
        payload = json.dumps(mapping, ensure_ascii=True, sort_keys=True).encode('utf-8')
        self._vault.merge_value_map(_TENANT, thread_id, payload)

    def _load_sync(self, thread_id: str) -> dict[str, str]:
        try:
            payload = self._vault.load_value_map(_TENANT, thread_id)
        except self._errors.record_not_found:
            return {}
        decoded = cast('dict[str, object]', json.loads(payload.decode('utf-8')))
        return {key: value for key, value in decoded.items() if isinstance(value, str)}


def merged_private_data(
    stored: Mapping[str, str],
    supplied: Mapping[object, object] | None,
) -> dict[object, object]:
    """Merge a thread's persisted map under the caller's explicit one.

    The caller's per-invoke values win: an explicit argument is the customer
    changing their mind for this turn, and a store must never override that.
    """
    merged: dict[object, object] = {}
    merged.update(stored)
    if supplied:
        merged.update(supplied)
    return merged


def persistable_pairs(supplied: Mapping[object, object] | None) -> dict[str, str]:
    """Return only the string-to-string pairs of a per-invoke private_data map.

    Placeholder maps are string to string by construction; anything else in the
    mapping is per-invoke structure that has no stable meaning at rest, so it
    passes through the call but is never persisted.
    """
    if not supplied:
        return {}
    return {
        key: value
        for key, value in supplied.items()
        if isinstance(key, str) and isinstance(value, str)
    }


class _VaultErrors:
    """Resolved exception types from the native vault wheel."""

    def __init__(self, record_not_found: type[Exception]) -> None:
        self.record_not_found = record_not_found


def _local_vault(path: str, secret: bytes) -> Any:
    module = _vault_module()
    vault_cls = cast('Any', module).LocalVault
    if not callable(getattr(vault_cls, 'merge_value_map', None)):
        message = (
            'EncryptedFilePrivateDataStore requires a private_data_vault wheel with '
            'atomic merge_value_map support. Rebuild and install the current '
            'libraries/maivn-private-data-vault wheel.'
        )
        raise TypeError(message)
    return vault_cls(path, secret)


def _vault_errors() -> _VaultErrors:
    module = _vault_module()
    return _VaultErrors(record_not_found=cast('Any', module).RecordNotFound)


def _vault_module() -> object:
    try:
        return import_module('private_data_vault')
    except ModuleNotFoundError as exc:
        message = (
            'EncryptedFilePrivateDataStore needs the private_data_vault wheel '
            '(built from libraries/maivn-private-data-vault). Install it into '
            'this environment, or use InMemoryPrivateDataStore for a '
            'non-durable store.'
        )
        raise ModuleNotFoundError(message) from exc


__all__ = [
    'EncryptedFilePrivateDataStore',
    'InMemoryPrivateDataStore',
    'PrivateDataStore',
    'merged_private_data',
    'persistable_pairs',
]
