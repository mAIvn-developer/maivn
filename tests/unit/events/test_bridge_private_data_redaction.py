"""A raw ``PrivateData.value`` never leaves the EventBridge.

``maivn.PrivateData`` carries the RAW private value. Every bridge output - the
live SSE frame, ``get_history()``, the replay a reconnecting client receives and
the ``on_packet`` observer - must carry the descriptor
``{"__private__": true, "label": ..., "pii_type": ...}`` instead, for both
audiences and at any nesting depth, without mutating the caller's objects.

No async pytest plugin is configured for this package, so each test drives its
coroutine with ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel, RootModel

from maivn import PrivateData
from maivn.events import EventBridge, UIEvent
from maivn.events._bridge.serialization import build_safe_event_payload, safe_json_dumps

if TYPE_CHECKING:
    from collections.abc import Callable

    from maivn.events import BridgeAudience

RAW = '4111-1111-1111-1111'
DESCRIPTOR: dict[str, object] = {
    '__private__': True,
    'label': 'Customer card',
    'pii_type': 'credit_card',
}
AUDIENCES: tuple[BridgeAudience, ...] = ('internal', 'frontend_safe')
# ``final`` is a known, normalized type; the custom type takes the unknown-type
# path, which ``frontend_safe`` scrubs generically.
EVENT_TYPES = ('final', 'custom_private_event')


def _card() -> PrivateData:
    return PrivateData(
        value=RAW,
        name='card_number',
        pii_type='credit_card',
        label='Customer card',
    )


class _Holder(BaseModel):
    card: PrivateData
    note: str = 'kept'


class _Outer(BaseModel):
    holder: _Holder
    cards: list[PrivateData]


class _Cards(RootModel[list[PrivateData]]):
    pass


class _Plain(BaseModel):
    note: str = 'kept'


@dataclasses.dataclass
class _Record:
    card: PrivateData
    holder: _Holder | None = None
    note: str = 'kept'


def _top_level() -> dict[str, object]:
    return {'card': _card()}


def _dict_and_list() -> dict[str, object]:
    return {'billing': {'cards': [_card(), {'backup': _card()}]}}


def _tuple_and_set() -> dict[str, object]:
    # Frozen pydantic models hash at runtime; the stubs do not say so.
    return {'pair': (_card(), 'kept'), 'bag': {_card()}}  # pyright: ignore[reportUnhashable]


def _pydantic_model() -> dict[str, object]:
    return {'order': _Outer(holder=_Holder(card=_card()), cards=[_card()])}


def _dataclass() -> dict[str, object]:
    return {'record': _Record(card=_card(), holder=_Holder(card=_card()))}


def _root_model() -> dict[str, object]:
    return {'wallet': _Cards([_card()])}


# Payload builder and the number of PrivateData instances it holds.
PAYLOADS: dict[str, tuple[Callable[[], dict[str, object]], int]] = {
    'top_level': (_top_level, 1),
    'dict_and_list': (_dict_and_list, 2),
    'tuple_and_set': (_tuple_and_set, 2),
    'pydantic_model': (_pydantic_model, 2),
    'dataclass': (_dataclass, 2),
    'root_model': (_root_model, 1),
}


def _descriptors(node: object) -> list[dict[str, object]]:
    """Collect every private-data descriptor in a parsed JSON document."""
    if isinstance(node, dict):
        mapping = cast('dict[str, object]', node)
        if '__private__' in mapping:
            return [mapping]
        return [found for item in mapping.values() for found in _descriptors(item)]
    if isinstance(node, list):
        return [found for item in cast('list[object]', node) for found in _descriptors(item)]
    return []


def _assert_redacted(serialized: str, *, expected: int) -> None:
    assert RAW not in serialized
    found = _descriptors(json.loads(serialized))
    assert len(found) == expected
    assert all(descriptor == DESCRIPTOR for descriptor in found)


def _emit(audience: BridgeAudience, event_type: str, extra: dict[str, object]) -> EventBridge:
    bridge = EventBridge('private-data-session', audience=audience)
    asyncio.run(bridge.emit(event_type, {'response': 'done', **extra}))
    return bridge


def _live_sse_data(bridge: EventBridge) -> str:
    frame = bridge.stream_queue_get_nowait().to_sse()
    return cast('str', frame['data'])


@pytest.mark.parametrize('payload_name', sorted(PAYLOADS))
@pytest.mark.parametrize('event_type', EVENT_TYPES)
@pytest.mark.parametrize('audience', AUDIENCES)
def test_live_sse_frame_carries_descriptor_not_value(
    audience: BridgeAudience,
    event_type: str,
    payload_name: str,
) -> None:
    """The live SSE frame shows the descriptor wherever PrivateData sat."""
    build, expected = PAYLOADS[payload_name]
    bridge = _emit(audience, event_type, build())

    _assert_redacted(_live_sse_data(bridge), expected=expected)


@pytest.mark.parametrize('payload_name', sorted(PAYLOADS))
@pytest.mark.parametrize('event_type', EVENT_TYPES)
@pytest.mark.parametrize('audience', AUDIENCES)
def test_history_json_dumps_carries_descriptor_not_value(
    audience: BridgeAudience,
    event_type: str,
    payload_name: str,
) -> None:
    """``get_history()`` holds descriptors, so a plain JSON encoder cannot leak."""
    build, expected = PAYLOADS[payload_name]
    bridge = _emit(audience, event_type, build())

    _assert_redacted(json.dumps(bridge.get_history(), default=str), expected=expected)


@pytest.mark.parametrize('payload_name', sorted(PAYLOADS))
@pytest.mark.parametrize('audience', AUDIENCES)
def test_history_through_fastapi_encoder_carries_descriptor_not_value(
    audience: BridgeAudience,
    payload_name: str,
) -> None:
    """FastAPI's encoder, which dumps models, sees descriptors in history."""
    encoders = pytest.importorskip('fastapi.encoders')
    jsonable_encoder = cast('Callable[[object], object]', encoders.jsonable_encoder)
    build, expected = PAYLOADS[payload_name]
    bridge = _emit(audience, 'final', build())

    _assert_redacted(json.dumps(jsonable_encoder(bridge.get_history())), expected=expected)


@pytest.mark.parametrize('audience', AUDIENCES)
def test_replay_and_snapshot_carry_descriptor_not_value(audience: BridgeAudience) -> None:
    """A reconnecting client replays descriptors, as does the history snapshot."""
    bridge = _emit(audience, 'final', _dataclass())

    async def _replay() -> list[str]:
        frames: list[str] = []
        async for frame in bridge.generate_sse():
            data = frame.get('data')
            if isinstance(data, str):
                frames.append(data)
        return frames

    frames = asyncio.run(_replay())
    assert len(frames) == 1
    _assert_redacted(frames[0], expected=2)
    snapshot = [event.to_sse() for event in bridge.stream_history_snapshot()]
    _assert_redacted(cast('str', snapshot[0]['data']), expected=2)


@pytest.mark.parametrize('audience', AUDIENCES)
def test_tool_event_args_and_result_carry_descriptor_not_value(audience: BridgeAudience) -> None:
    """Tool arguments and results go through the same redaction."""
    bridge = EventBridge('private-data-session', audience=audience)
    asyncio.run(
        bridge.emit_tool_event(
            tool_name='charge_card',
            tool_id='tool-1',
            status='completed',
            args={'card': _card()},
            result=_Holder(card=_card()),
        )
    )

    sse = _live_sse_data(bridge)
    history = json.dumps(bridge.get_history(), default=str)
    for serialized in (sse, history):
        assert RAW not in serialized
        found = _descriptors(json.loads(serialized))
        assert found
        assert all(descriptor == DESCRIPTOR for descriptor in found)


@pytest.mark.parametrize('audience', AUDIENCES)
def test_on_packet_observer_sees_descriptor_not_value(audience: BridgeAudience) -> None:
    """A durable-write observer never receives the raw value."""
    seen: list[UIEvent] = []
    bridge = EventBridge('private-data-session', audience=audience, on_packet=seen.append)
    asyncio.run(bridge.emit('final', {'response': 'done', **_pydantic_model()}))

    assert len(seen) == 1
    _assert_redacted(json.dumps(seen[0].to_dict(), default=str), expected=2)


@pytest.mark.parametrize('payload_name', sorted(PAYLOADS))
@pytest.mark.parametrize('audience', AUDIENCES)
def test_caller_objects_are_not_mutated(audience: BridgeAudience, payload_name: str) -> None:
    """Redaction builds new structures; the caller's payload is unchanged."""
    build, _ = PAYLOADS[payload_name]
    payload = build()
    before = copy.deepcopy(payload)
    bridge = _emit(audience, 'final', payload)
    _ = _live_sse_data(bridge)
    _ = json.dumps(bridge.get_history(), default=str)

    assert payload == before
    assert RAW in repr(payload)


def test_dataclass_and_model_values_keep_their_identity() -> None:
    """The caller's dataclass, model and PrivateData objects are not replaced."""
    card = _card()
    holder = _Holder(card=card)
    record = _Record(card=card, holder=holder)
    data: dict[str, object] = {'record': record, 'holder': holder, 'card': card}

    _ = UIEvent(type='final', data=data).to_sse()

    assert data['record'] is record
    assert data['holder'] is holder
    assert record.card is card
    assert record.holder is holder
    assert holder.card is card
    assert card.value == RAW


@pytest.mark.parametrize(
    ('fields', 'label'),
    [
        (
            {'label': 'Customer card', 'name': 'card_number', 'pii_type': 'credit_card'},
            'Customer card',
        ),
        ({'name': 'card_number', 'pii_type': 'credit_card'}, 'card_number'),
        ({'pii_type': 'credit_card'}, 'credit_card'),
        ({}, 'private data'),
    ],
)
def test_descriptor_label_falls_back_like_booth(fields: dict[str, str], label: str) -> None:
    """The label falls back label, name, pii_type, then "private data"."""
    item = PrivateData(value=RAW, **fields)
    sse = cast('str', UIEvent(type='final', data={'item': item}).to_sse()['data'])

    assert RAW not in sse
    payload = cast('dict[str, dict[str, object]]', json.loads(sse))
    assert payload['data']['item'] == {
        '__private__': True,
        'label': label,
        'pii_type': fields.get('pii_type'),
    }


def test_serializer_backstop_redacts_without_the_bridge() -> None:
    """The SSE serializer redacts even when a payload never passed through UIEvent."""
    payload: dict[str, object] = {'card': _card(), 'holder': _Holder(card=_card())}

    _assert_redacted(safe_json_dumps(payload), expected=2)
    _assert_redacted(
        build_safe_event_payload(payload, event_id='e1', event_type='final', timestamp='ts'),
        expected=2,
    )


def test_payload_without_private_data_is_untouched() -> None:
    """Payloads without PrivateData keep their identity and serialization."""
    plain = _Plain()
    data: dict[str, object] = {'model': plain, 'items': [1, 'two', {'three': 3}]}

    event = UIEvent(type='final', data=data)

    assert event.data is data
    assert event.data['model'] is plain
    sse = cast('dict[str, object]', json.loads(cast('str', event.to_sse()['data'])))
    assert sse['data'] == {'model': {'note': 'kept'}, 'items': [1, 'two', {'three': 3}]}


def test_root_model_keeps_its_root_shape() -> None:
    """A root model holding PrivateData serializes as its root, as ``model_dump()`` does."""
    event = UIEvent(type='final', data={'wallet': _Cards([_card()])})
    payload = cast('dict[str, dict[str, object]]', json.loads(cast('str', event.to_sse()['data'])))

    assert payload['data']['wallet'] == [DESCRIPTOR]
