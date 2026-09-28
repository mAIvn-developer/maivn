"""The deliberate contract between the client store and server-side custody.

There are now two per-thread placeholder maps, and E2E-048 was really the
absence of a written rule about which is which. The store merged across every
call sharing a `thread_id` and said nothing about it, so a caller could not tell
what would be re-sent on a later turn or which map a value came from.

The contract as shipped:

* the CLIENT store holds the caller's OWN declared values, per thread. Nothing
  the server allocates ever enters it - the client is never handed the server's
  map, so it cannot learn `pii_email_1` even in principle;
* SERVER custody holds the complete run map, declared and discovered alike, and
  it is what makes a prior turn's discovered keys resolve;
* where both hold a key, the CALLER wins. The client's map travels in the
  request, and the server merges custody underneath it;
* only string pairs rest, on both sides, so the two stores describe a thread the
  same way;
* persistence is opt-in and `forget` ends it.
"""

from __future__ import annotations

import asyncio

from maivn import InMemoryPrivateDataStore
from maivn._internal.client import Client
from maivn._internal.private_data_store import merged_private_data, persistable_pairs


def _client(store: InMemoryPrivateDataStore | None) -> Client:
    return Client(
        api_key='mvn_test_key',
        base_url='http://127.0.0.1:1',
        private_data_store=store,
    )


def test_the_store_holds_only_what_the_caller_declared() -> None:
    """A server-allocated key cannot reach the client store, by construction.

    The client persists `persistable_pairs(supplied)` - the map the CALLER
    passed to this call - and never a map the server sends back. So a discovered
    key like `pii_email_1` stays server-side, which is the division of labour
    custody exists to make true.
    """

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        client = _client(store)
        await client._thread_private_data(  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            'thread-1',
            {'serial_number': 'SN-8831'},
        )
        assert await store.resolve('thread-1', ()) == {'serial_number': 'SN-8831'}

    asyncio.run(exercise())


def test_a_turns_own_value_outranks_the_one_the_thread_was_carrying() -> None:
    """Same precedence on both sides: this turn's declaration is the current truth.

    The server applies the identical rule to custody, so a caller who changes a
    value gets the new one in the model context, in tool arguments and in the
    response - not a mix of old and new depending on which store answered.
    """

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        client = _client(store)
        await client._thread_private_data('thread-1', {'serial_number': 'SN-1'})  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        second = await client._thread_private_data(  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            'thread-1',
            {'serial_number': 'SN-2'},
        )
        assert second == {'serial_number': 'SN-2'}
        assert await store.resolve('thread-1', ()) == {'serial_number': 'SN-2'}

    asyncio.run(exercise())


def test_forgetting_a_thread_stops_it_being_re_sent() -> None:
    """The documented way out. Without it, opting in would be one-way."""

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        client = _client(store)
        await client._thread_private_data('thread-1', {'serial_number': 'SN-1'})  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        await store.forget('thread-1')
        assert await client._thread_private_data('thread-1', None) is None  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]

    asyncio.run(exercise())


def test_the_two_stores_agree_on_what_may_rest() -> None:
    """String pairs only, client and server alike.

    A run-local integer that came back as a string on the next turn would change
    a tool argument's type mid-conversation, so neither side reshapes one in
    passing; it stays the caller's to re-supply.
    """
    assert persistable_pairs({'serial_number': 'SN-1', 'attempts': 3}) == {
        'serial_number': 'SN-1',
    }


def test_a_thread_id_is_required_before_anything_is_remembered() -> None:
    """No thread means no conversation to scope a map to, so nothing rests."""

    async def exercise() -> None:
        store = InMemoryPrivateDataStore()
        client = _client(store)
        supplied: dict[object, object] = {'serial_number': 'SN-1'}
        assert await client._thread_private_data(None, supplied) is supplied  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        assert await store.resolve('thread-1', ()) == {}

    asyncio.run(exercise())


def test_the_precedence_rule_is_the_one_the_server_applies() -> None:
    """Stated once as a function so client and server cannot drift apart."""
    assert merged_private_data({'a': 'stored', 'b': 'stored'}, {'a': 'supplied'}) == {
        'a': 'supplied',
        'b': 'stored',
    }
