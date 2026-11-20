"""The deliberate contract for where a thread's private values rest between turns.

E2E-048 was really the absence of a written rule: the store merged across every
call sharing a `thread_id` and said nothing about it, so a caller could not tell
what would be re-sent on a later turn or which map a value came from.

The contract since the owner ruling of 2026-11-18 (private values rest only in
the customer's own vault, or in our database while a run waits for a person):

* the CLIENT store is the one place a thread's map rests. It holds the caller's
  declared values AND the pairs the server's shield minted, which each run's
  final frame hands back and the client strips from that frame;
* the platform keeps no conversation map after a run ends;
* where the call and the store both hold a key, the call wins;
* only string pairs rest;
* persistence is opt-in and `forget` ends it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from maivn import InMemoryPrivateDataStore
from maivn._internal.client import Client
from maivn._internal.models import StreamEvent
from maivn._internal.private_data_store import (
    CALLER_HELD_PRIVATE_DATA_KEY,
    merged_private_data,
    persistable_pairs,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pytest


def _client(store: InMemoryPrivateDataStore | None) -> Client:
    return Client(
        api_key='mvn_test_key',
        base_url='http://127.0.0.1:1',
        private_data_store=store,
    )


def test_a_call_remembers_what_the_caller_declared() -> None:
    """The map passed to a call is kept for the thread."""

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


# MARK: - The final frame hands the shield's keys to this store


_EMAIL = 'dana.lee@example.com'


def _final(payload: dict[str, object]) -> StreamEvent:
    return StreamEvent(
        position=7,
        event_type='final',
        data={'type': 'final', 'session_id': 'ses-1', 'payload': payload},
    )


def _handed_over(**extra: object) -> StreamEvent:
    return _final(
        {
            'message': 'Sent to ' + _EMAIL,
            CALLER_HELD_PRIVATE_DATA_KEY: {'pii_email_1': _EMAIL},
            **extra,
        }
    )


def _keep(client: Client, event: StreamEvent, thread_id: str | None) -> StreamEvent:
    return asyncio.run(
        client._keep_caller_held_private_data(event, thread_id=thread_id)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    )


def test_the_final_frame_hands_the_shields_keys_to_the_threads_store() -> None:
    """The next call on the thread sends `pii_email_1` back; nothing else keeps it."""
    store = InMemoryPrivateDataStore()
    client = _client(store)

    kept = _keep(client, _handed_over(), 'thread-1')

    assert asyncio.run(store.resolve('thread-1', ())) == {'pii_email_1': _EMAIL}
    assert CALLER_HELD_PRIVATE_DATA_KEY not in kept.payload
    assert kept.data['payload'] == {'message': 'Sent to ' + _EMAIL}
    next_call = asyncio.run(
        client._thread_private_data('thread-1', None)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    )
    assert next_call == {'pii_email_1': _EMAIL}


def test_a_call_without_a_thread_keeps_nothing_but_still_strips_the_frame() -> None:
    """Same rule as declared values: no named thread, nothing persists."""
    store = InMemoryPrivateDataStore()
    kept = _keep(_client(store), _handed_over(thread_id='thr-server'), None)
    assert asyncio.run(store.resolve('thr-server', ())) == {}
    assert CALLER_HELD_PRIVATE_DATA_KEY not in kept.payload


def test_without_a_store_the_values_are_dropped_from_the_frame_and_kept_nowhere() -> None:
    """`private_data_store=None` keeps values within the call that carried them."""
    kept = _keep(_client(None), _handed_over(), 'thread-1')
    assert CALLER_HELD_PRIVATE_DATA_KEY not in kept.payload


def test_frames_without_a_hand_over_pass_through_untouched() -> None:
    """Ordinary frames are the same object; nothing is copied or opened."""
    event = _final({'message': 'done'})
    assert _keep(_client(InMemoryPrivateDataStore()), event, 'thread-1') is event


class _BrokenStore(InMemoryPrivateDataStore):
    async def remember(self, thread_id: str, mapping: Mapping[str, str]) -> None:
        del thread_id, mapping
        message = 'vault unavailable'
        raise OSError(message)

    async def resolve(self, thread_id: str, placeholders: Sequence[str]) -> dict[str, str]:
        del thread_id, placeholders
        return {}


def test_a_store_that_cannot_keep_them_does_not_cost_this_turns_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the next turn loses resolution; the warning names no value."""
    with caplog.at_level(logging.WARNING):
        kept = _keep(_client(_BrokenStore()), _handed_over(), 'thread-1')
    assert kept.data['payload'] == {'message': 'Sent to ' + _EMAIL}
    assert 'could not be kept' in caplog.text
    assert _EMAIL not in caplog.text
