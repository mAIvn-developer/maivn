"""A ``PrivateData`` value never reaches the event wire, whatever holds it (U05-D22, D10).

Mappings other than ``dict``, namespaces, deques and models or dataclasses holding
them used to reach the serializer's ``str(o)`` fallback, and ``PrivateData``'s repr
carried the raw value. Every case is driven through the real wire path
(``UIEvent.to_sse()``) and through history, for both audiences.

No async pytest plugin is configured for this package, so each test drives its
coroutine with ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import dataclasses
import datetime
import fractions
import ipaddress
import json
import pathlib
import types
import zoneinfo
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, NamedTuple, cast

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    GetCoreSchemaHandler,
    IPvAnyAddress,
    PlainSerializer,
    PrivateAttr,
    SecretStr,
    SerializerFunctionWrapHandler,
    computed_field,
    field_serializer,
    model_serializer,
)
from pydantic.dataclasses import dataclass as pydantic_dataclass
from pydantic_core import core_schema

from maivn import PIIWhitelistEntry, PrivateData, RedactedMessage
from maivn.events import EventBridge, UIEvent
from maivn.events._bridge.serialization import safe_json_dumps

if TYPE_CHECKING:
    from collections.abc import Callable

    from typing_extensions import Never

    from maivn.events import BridgeAudience

RAW = '4111-1111-1111-1111'
SSN = '123-45-6789'
DESCRIPTOR: dict[str, object] = {
    '__private__': True,
    'label': 'Customer card',
    'pii_type': 'credit_card',
}
AUDIENCES: tuple[BridgeAudience, ...] = ('internal', 'frontend_safe')


def _card() -> PrivateData:
    return PrivateData(value=RAW, name='card_number', pii_type='credit_card', label='Customer card')


class _DequeHolder(BaseModel):
    cards: collections.deque[PrivateData]


class _NamespaceHolder(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    extra: types.SimpleNamespace


@dataclasses.dataclass
class _ProxyRecord:
    lookup: types.MappingProxyType[str, object]


class _MaskedSibling(BaseModel):
    card: PrivateData
    ssn: str

    @field_serializer('ssn')
    def _mask_ssn(self, value: str) -> str:
        return '***-**-' + value[-4:]


class _Opaque:
    def __str__(self) -> str:
        return RAW


CONTAINERS: dict[str, Callable[[], object]] = {
    'namespace': lambda: types.SimpleNamespace(card=_card()),
    'mapping_proxy': lambda: types.MappingProxyType({'card': _card()}),
    'user_dict': lambda: collections.UserDict({'card': _card()}),
    'deque': lambda: collections.deque([_card()]),
    'model_with_deque': lambda: _DequeHolder(cards=collections.deque([_card()])),
    'model_with_namespace': lambda: _NamespaceHolder(extra=types.SimpleNamespace(card=_card())),
    'dataclass_with_proxy': lambda: _ProxyRecord(types.MappingProxyType({'card': _card()})),
    'nested_in_list': lambda: [types.SimpleNamespace(inner=collections.deque([_card()]))],
}


def _descriptors(node: object) -> list[dict[str, object]]:
    if isinstance(node, dict):
        mapping = cast('dict[str, object]', node)
        if '__private__' in mapping:
            return [mapping]
        return [found for item in mapping.values() for found in _descriptors(item)]
    if isinstance(node, list):
        return [found for item in cast('list[object]', node) for found in _descriptors(item)]
    return []


@pytest.mark.parametrize('name', sorted(CONTAINERS))
def test_ui_event_wire_shows_descriptor_for_any_container(name: str) -> None:
    """``UIEvent.to_sse()`` emits the descriptor, never the value, inside every container."""
    sse = cast('str', UIEvent(type='final', data={'x': CONTAINERS[name]()}).to_sse()['data'])

    assert RAW not in sse
    assert _descriptors(json.loads(sse)) == [DESCRIPTOR]


@pytest.mark.parametrize('name', sorted(CONTAINERS))
@pytest.mark.parametrize('audience', AUDIENCES)
def test_bridge_frame_and_history_hide_the_value(audience: BridgeAudience, name: str) -> None:
    """Live frame and history carry no raw value for either audience."""
    bridge = EventBridge('private-container-session', audience=audience)
    asyncio.run(bridge.emit('final', {'response': 'done', 'x': CONTAINERS[name]()}))

    frame = cast('str', bridge.stream_queue_get_nowait().to_sse()['data'])
    history = json.dumps(bridge.get_history(), default=str)
    for serialized in (frame, history, _fastapi_history(bridge)):
        assert RAW not in serialized
    assert _descriptors(json.loads(frame)) == [DESCRIPTOR]


def _fastapi_history(bridge: EventBridge) -> str:
    """Serialize history the way a FastAPI route returning it would (it dumps models raw)."""
    encoders = pytest.importorskip('fastapi.encoders')
    jsonable_encoder = cast('Callable[[object], object]', encoders.jsonable_encoder)
    return json.dumps(jsonable_encoder(bridge.get_history()))


def test_a_model_with_serializers_beside_private_data_is_emitted_by_type_name() -> None:
    """Serializer code on a model holding PrivateData could reach the value: type name only."""
    holder = _MaskedSibling(card=_card(), ssn=SSN)
    sse = cast('str', UIEvent(type='final', data={'holder': holder}).to_sse()['data'])

    assert RAW not in sse
    assert SSN not in sse
    payload = cast('dict[str, dict[str, object]]', json.loads(sse))
    assert payload['data']['holder'] == '_MaskedSibling'


@pytest.mark.parametrize(
    'value',
    [
        datetime.time(12, 30, tzinfo=datetime.timezone.utc),
        datetime.timedelta(hours=1, seconds=5),
        pathlib.PurePosixPath('/data/report.txt'),
        pathlib.PureWindowsPath(r'C:\data\report.txt'),
        pathlib.Path('data') / 'report.txt',
        ipaddress.IPv4Address('10.0.0.1'),
        ipaddress.IPv6Address('::1'),
        ipaddress.IPv4Network('10.0.0.0/8'),
        ipaddress.IPv6Network('2001:db8::/32'),
        ipaddress.IPv4Interface('10.0.0.1/24'),
        ipaddress.IPv6Interface('2001:db8::1/64'),
        fractions.Fraction(1, 3),
        zoneinfo.ZoneInfo('UTC'),
    ],
    ids=repr,
)
def test_stdlib_value_types_keep_their_text(value: object) -> None:
    """Plain stdlib values that cannot hold a PrivateData go out as ``str(value)``, as before."""
    payload = cast('dict[str, object]', json.loads(safe_json_dumps({'value': value})))

    assert payload == {'value': str(value)}


def test_serializer_last_fallback_is_the_type_name_not_str() -> None:
    """An object with no structured form serializes as its type name, never ``str(o)``."""
    payload = cast('dict[str, object]', json.loads(safe_json_dumps({'opaque': _Opaque()})))

    assert payload == {'opaque': '_Opaque'}


@pytest.mark.parametrize(
    'build',
    [
        lambda: PrivateData(value=SSN, name='ssn'),
        lambda: RedactedMessage(content=f'My SSN is {SSN}', known_pii_values=[SSN]),
        lambda: RedactedMessage(content='x', known_pii_values=[PrivateData(value=SSN)]),
        lambda: PIIWhitelistEntry(value=SSN, justification='Support desk number.'),
    ],
    ids=['private_data', 'redacted_message', 'redacted_message_private', 'whitelist_entry'],
)
def test_privacy_model_reprs_never_show_the_value(build: Callable[[], BaseModel]) -> None:
    """``repr`` and ``str`` of the privacy models leave the private value out."""
    model = build()

    assert SSN not in repr(model)
    assert SSN not in str(model)


# MARK: Review follow-ups (dump-only carriers, masks, hostile containers)


class _PrivateAttrExposed(BaseModel):
    _card: PrivateData = PrivateAttr()

    @model_serializer
    def _dump(self) -> dict[str, object]:
        return {'card': self._card}


class _SerializerReturnsPrivate(BaseModel):
    ssn: str

    @field_serializer('ssn')
    def _wrap(self, value: str) -> PrivateData:
        return PrivateData(value=value, label='Customer card', pii_type='credit_card')


class _BrokenComputed(_MaskedSibling):
    @computed_field
    @property
    def boom(self) -> str:
        raise RuntimeError


_Masked = Annotated[str, PlainSerializer(lambda _value: 'MASK')]


class _MixedList(BaseModel):
    items: list[_Masked | PrivateData]


@pydantic_dataclass
class _PydanticRecord:
    card: PrivateData
    ssn: str

    @field_serializer('ssn')
    def _mask_ssn(self, value: str) -> str:
        return '***-**-' + value[-4:]


class _SelfMasking(str):
    """A string type whose own core schema masks it when serialized."""

    __slots__ = ()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: object, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(lambda _v: '***'),
        )


class _SelfMaskingSibling(BaseModel):
    card: PrivateData
    ssn: _SelfMasking


class _SecretSibling(BaseModel):
    card: PrivateData
    ssn: SecretStr


class _BuiltinTypesBeside(BaseModel):
    card: PrivateData
    ip: IPvAnyAddress
    items: collections.deque[int]


class _FailingSequence(Sequence[object]):
    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: object) -> Never:
        message = 'db closed'
        raise ValueError(message)


class _HugeSequence(Sequence[int]):
    def __len__(self) -> int:
        return 10**12

    # A virtual sequence answers any index; slices are never asked for here.
    def __getitem__(self, index: int) -> int:  # pyright: ignore[reportIncompatibleMethodOverride]
        return index


class _EndlessSequence(Sequence[int]):
    """Claims one item, but ``Sequence.__iter__`` stops only at an IndexError that never comes."""

    def __len__(self) -> int:
        return 1

    def __getitem__(self, index: int) -> int:  # pyright: ignore[reportIncompatibleMethodOverride]
        return index


class _LyingMapping(Mapping[str, int]):
    """Holds one item but claims 10**12, which ``list()`` would preallocate for."""

    def __len__(self) -> int:
        return 10**12

    def __getitem__(self, key: str) -> int:
        if key != 'a':
            raise KeyError(key)
        return 1

    def __iter__(self) -> Iterator[str]:
        return iter(('a',))


class _Point(NamedTuple):
    x: int
    y: int


def _exposed() -> _PrivateAttrExposed:
    model = _PrivateAttrExposed()
    model._card = _card()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage] - the carrier.
    return model


class _InjectedBesideDumpOnly(_SerializerReturnsPrivate):
    private_data_injected: dict[str, str]


class _RaisingSerializer(BaseModel):
    ssn: str
    bad: int = 0

    @field_serializer('bad')
    def _fail(self, _value: int) -> int:
        raise RuntimeError


class _RaisingBesidePrivate(_RaisingSerializer):
    card: PrivateData


class _OwnSerializerCard(PrivateData):
    """A subclass whose own model serializer replaces PrivateData's."""

    @model_serializer(mode='wrap')
    def _own(self, handler: SerializerFunctionWrapHandler) -> object:
        return handler(self)


def _exposed_subclass() -> _PrivateAttrExposed:
    model = _PrivateAttrExposed()
    model._card = _OwnSerializerCard(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        value=RAW, label='Customer card', pii_type='credit_card'
    )
    return model


class _DuckModel:
    """Not a pydantic model: its ``model_dump`` delegates to one."""

    def model_dump(self) -> object:
        return _Inner(card=_card()).model_dump()


class _DuckLegacy:
    """A legacy ``dict()`` object that delegates to a pydantic dump."""

    def dict(self) -> object:
        return _Inner(card=_card()).model_dump()


class _Inner(BaseModel):
    card: PrivateData


DUMP_ONLY_CARRIERS: dict[str, Callable[[], object]] = {
    'private_attr_via_model_serializer': _exposed,
    'subclass_with_its_own_serializer': _exposed_subclass,
    'duck_model_dump_delegating': _DuckModel,
    'duck_dict_delegating': _DuckLegacy,
    'field_serializer_returning_private': lambda: _SerializerReturnsPrivate(ssn=RAW),
    # The frontend-safe scrub rebuilds this model from its dump to summarize the injected field.
    'injected_field_beside_dump_only': lambda: _InjectedBesideDumpOnly(
        ssn=RAW, private_data_injected={'k': 'v'}
    ),
}


@pytest.mark.parametrize('name', sorted(DUMP_ONLY_CARRIERS))
@pytest.mark.parametrize('audience', AUDIENCES)
def test_private_data_produced_while_dumping_never_shows_its_value(
    audience: BridgeAudience, name: str
) -> None:
    """A PrivateData that only appears in a model's dump still becomes its descriptor."""
    bridge = EventBridge('private-dump-session', audience=audience)
    asyncio.run(bridge.emit('final', {'response': 'done', 'x': DUMP_ONLY_CARRIERS[name]()}))

    frame = cast('str', bridge.stream_queue_get_nowait().to_sse()['data'])
    assert RAW not in frame
    if name != 'subclass_with_its_own_serializer':
        # A subclass that replaces the model serializer keeps its shape; its value is masked.
        assert _descriptors(json.loads(frame)) == [DESCRIPTOR]


@pytest.mark.parametrize(
    'build',
    [
        lambda: _BrokenComputed(card=_card(), ssn=SSN),
        lambda: _MixedList(items=[SSN, _card()]),
        lambda: _PydanticRecord(card=_card(), ssn=SSN),
        lambda: _SelfMaskingSibling(card=_card(), ssn=_SelfMasking(SSN)),
        lambda: _SecretSibling(card=_card(), ssn=SecretStr(SSN)),
    ],
    ids=[
        'dump_raises',
        'masked_items_next_to_private',
        'pydantic_dataclass',
        'self_masking_type',
        'secret_str',
    ],
)
def test_masks_survive_next_to_private_data(build: Callable[[], object]) -> None:
    """A mask applied by pydantic is never undone by redacting a PrivateData beside it."""
    sse = cast('str', UIEvent(type='final', data={'x': build()}).to_sse()['data'])

    assert SSN not in sse
    assert RAW not in sse


@pytest.mark.parametrize(
    'build',
    [lambda: _RaisingSerializer(ssn=SSN), lambda: _RaisingBesidePrivate(ssn=SSN, card=_card())],
    ids=['plain', 'beside_private_data'],
)
def test_model_whose_dump_fails_is_emitted_by_type_name(build: Callable[[], BaseModel]) -> None:
    """A model that cannot be dumped never falls back to its fields or its text."""
    model = build()
    sse = cast('str', UIEvent(type='final', data={'x': model}).to_sse()['data'])

    payload = cast('dict[str, dict[str, object]]', json.loads(sse))
    assert payload['data']['x'] == type(model).__name__


class _FieldUnwraps(BaseModel):
    ssn: PrivateData

    @field_serializer('ssn')
    def _unwrap(self, value: PrivateData) -> object:
        return value.value


class _AnnotatedUnwraps(BaseModel):
    ssn: Annotated[PrivateData, PlainSerializer(lambda value: f'SSN {value.value}')]


class _ModelUnwraps(BaseModel):
    ssn: PrivateData

    @model_serializer
    def _dump(self) -> dict[str, object]:
        return {'ssn': self.ssn.value}


class _ComputedUnwraps(BaseModel):
    ssn: PrivateData

    @computed_field
    @property
    def shown(self) -> object:
        return self.ssn.value


class _FieldNormalizes(BaseModel):
    ssn: PrivateData

    @field_serializer('ssn')
    def _normalize(self, value: PrivateData) -> object:
        return str(value.value).replace('-', '')


class _FieldEncodes(BaseModel):
    ssn: PrivateData

    @field_serializer('ssn')
    def _encode(self, value: PrivateData) -> object:
        return base64.b64encode(str(value.value).encode()).decode()


class _HugeIntBeside(BaseModel):
    ssn: PrivateData
    big: int


def _ssn_card() -> PrivateData:
    return PrivateData(value=SSN, label='SSN', pii_type='ssn')


UNWRAPPING_MODELS: dict[str, Callable[[], BaseModel]] = {
    'field_serializer': lambda: _FieldUnwraps(ssn=_ssn_card()),
    'annotated_plain_serializer': lambda: _AnnotatedUnwraps(ssn=_ssn_card()),
    'model_serializer': lambda: _ModelUnwraps(ssn=_ssn_card()),
    'computed_field': lambda: _ComputedUnwraps(ssn=_ssn_card()),
    'field_serializer_normalizes': lambda: _FieldNormalizes(ssn=_ssn_card()),
    'field_serializer_encodes': lambda: _FieldEncodes(ssn=_ssn_card()),
}


@pytest.mark.parametrize('name', sorted(UNWRAPPING_MODELS))
@pytest.mark.parametrize('audience', AUDIENCES)
def test_a_serializer_that_unwraps_the_value_never_reaches_the_wire(
    audience: BridgeAudience, name: str
) -> None:
    """A model whose own serializer emits a PrivateData's value is emitted by type name."""
    model = UNWRAPPING_MODELS[name]()
    seen: list[UIEvent] = []
    bridge = EventBridge('private-unwrap-session', audience=audience, on_packet=seen.append)
    asyncio.run(bridge.emit('final', {'response': 'done', 'x': model}))

    frame = cast('str', bridge.stream_queue_get_nowait().to_sse()['data'])
    for serialized in (
        frame,
        json.dumps(bridge.get_history(), default=str),
        json.dumps(seen[0].to_dict(), default=str),
    ):
        assert SSN not in serialized
        assert SSN.replace('-', '') not in serialized
        assert base64.b64encode(SSN.encode()).decode() not in serialized
    payload = cast('dict[str, dict[str, object]]', json.loads(frame))
    assert payload['data']['x'] == type(model).__name__


class _SiblingReadsCarrier(BaseModel):
    ssn: PrivateData
    note: str = 'n'

    @field_serializer('note')
    def _leak(self, _value: str) -> str:
        return str(self.ssn.value).replace('-', '')


class _WrapSiblingReadsCarrier(BaseModel):
    ssn: PrivateData
    note: str = 'n'

    @field_serializer('note', mode='wrap')
    def _leak(self, value: str, handler: SerializerFunctionWrapHandler) -> str:
        return cast('str', handler(value)) + str(self.ssn.value).replace('-', '')


@pydantic_dataclass
class _DataclassSiblingReadsCarrier:
    ssn: PrivateData
    note: str = 'n'

    @field_serializer('note')
    def _leak(self, _value: str) -> str:
        return str(self.ssn.value).replace('-', '')


class _ExtraSiblingReadsCarrier(BaseModel):
    model_config = ConfigDict(extra='allow')
    note: str = 'n'

    @field_serializer('note')
    def _leak(self, _value: str) -> str:
        extra = self.__pydantic_extra__ or {}
        return str(cast('PrivateData', extra['ssn']).value).replace('-', '')


class _PrivateAliasReader(BaseModel):
    ssn: PrivateData
    note: str = 'n'
    _alias: PrivateData | None = PrivateAttr(default=None)

    def model_post_init(self, _context: object, /) -> None:
        self._alias = self.ssn

    @field_serializer('note')
    def _leak(self, _value: str) -> str:
        alias = cast('PrivateData', self._alias)
        return str(alias.value).replace('-', '')


@pydantic_dataclass
class _SelfCopyingRecord:
    ssn: PrivateData
    note: str = 'n'

    def __copy__(self) -> _SelfCopyingRecord:
        return self

    @field_serializer('note')
    def _leak(self, _value: str) -> str:
        return str(self.ssn.value).replace('-', '')


SIBLING_READERS: dict[str, Callable[[], object]] = {
    'private_attribute_alias': lambda: _PrivateAliasReader(ssn=_ssn_card()),
    'dataclass_copy_returns_self': lambda: _SelfCopyingRecord(ssn=_ssn_card()),
    'field_serializer': lambda: _SiblingReadsCarrier(ssn=_ssn_card()),
    'wrap_field_serializer': lambda: _WrapSiblingReadsCarrier(ssn=_ssn_card()),
    'pydantic_dataclass': lambda: _DataclassSiblingReadsCarrier(ssn=_ssn_card()),
    'extra_carrier': lambda: _ExtraSiblingReadsCarrier(ssn=_ssn_card()),  # pyright: ignore[reportCallIssue]
}


@pytest.mark.parametrize('name', sorted(SIBLING_READERS))
@pytest.mark.parametrize('audience', AUDIENCES)
def test_a_sibling_serializer_never_sees_the_carrier_value(
    audience: BridgeAudience, name: str
) -> None:
    """A serializer on another field reads only the carrier's value-free form."""
    bridge = EventBridge('private-sibling-session', audience=audience)
    asyncio.run(bridge.emit('final', {'response': 'done', 'x': SIBLING_READERS[name]()}))

    frame = cast('str', bridge.stream_queue_get_nowait().to_sse()['data'])
    history = json.dumps(bridge.get_history(), default=str)
    for serialized in (frame, history):
        assert SSN.replace('-', '') not in serialized
        assert SSN not in serialized


def test_redaction_never_changes_the_callers_dataclass() -> None:
    """Emitting a dataclass whose ``__copy__`` returns itself leaves it intact."""
    record = _SelfCopyingRecord(ssn=_ssn_card())

    _ = UIEvent(type='final', data={'x': record}).to_sse()

    assert isinstance(record.ssn, PrivateData)
    assert record.ssn.value == SSN


def test_a_huge_int_beside_private_data_never_breaks_the_emit() -> None:
    """An int too long to turn into text does not fail the emit."""
    model = _HugeIntBeside(ssn=_ssn_card(), big=10**5000)
    sse = cast('str', UIEvent(type='final', data={'x': model}).to_sse()['data'])

    assert SSN not in sse


def test_pydantic_builtin_serializers_do_not_count_as_model_code() -> None:
    """Fields whose serialization pydantic itself provides keep the model walkable."""
    model = _BuiltinTypesBeside.model_validate(
        {'card': _card(), 'ip': '10.0.0.1', 'items': collections.deque([1])}
    )
    sse = cast('str', UIEvent(type='final', data={'x': model}).to_sse()['data'])

    payload = cast('dict[str, dict[str, dict[str, object]]]', json.loads(sse))
    assert payload['data']['x']['card'] == DESCRIPTOR
    assert payload['data']['x']['items'] == [1]


@pytest.mark.parametrize('audience', AUDIENCES)
def test_hostile_sequences_never_break_the_emit(audience: BridgeAudience) -> None:
    """A sequence that fails or is too large to walk is emitted by type name."""
    bridge = EventBridge('private-hostile-session', audience=audience)
    asyncio.run(
        bridge.emit(
            'final', {'response': 'done', 'bad': _FailingSequence(), 'huge': _HugeSequence()}
        )
    )

    frame = cast(
        'dict[str, dict[str, object]]',
        json.loads(cast('str', bridge.stream_queue_get_nowait().to_sse()['data'])),
    )
    assert (frame['data']['bad'], frame['data']['huge']) == ('_FailingSequence', '_HugeSequence')


@pytest.mark.parametrize('audience', AUDIENCES)
def test_custom_containers_are_walked_only_to_a_bound(audience: BridgeAudience) -> None:
    """A custom container is read item by item up to a bound, whatever its ``__len__`` says.

    ``list()`` preallocates what ``__len__`` claims, and Linux overcommit lets a 10**12-slot
    preallocation succeed, so trusting it grows until the process is killed. A sequence
    that never ends is emitted by type name; a mapping that lies keeps its real items.
    """
    bridge = EventBridge('private-endless-session', audience=audience)
    asyncio.run(
        bridge.emit(
            'final', {'response': 'done', 'seq': _EndlessSequence(), 'map': _LyingMapping()}
        )
    )

    frame = cast(
        'dict[str, dict[str, object]]',
        json.loads(cast('str', bridge.stream_queue_get_nowait().to_sse()['data'])),
    )
    assert (frame['data']['seq'], frame['data']['map']) == ('_EndlessSequence', {'a': 1})


def test_plain_containers_without_private_data_keep_their_items() -> None:
    """Containers orjson does not encode keep a structured form instead of a type name."""
    payload: dict[str, object] = {
        'deque': collections.deque([1, 2]),
        'namespace': types.SimpleNamespace(a=1),
        'proxy': types.MappingProxyType({'b': 2}),
        'user_list': collections.UserList([3]),
        'user_string': collections.UserString('text'),
        'point': _Point(1, 2),
    }

    assert json.loads(safe_json_dumps(payload)) == {
        'deque': [1, 2],
        'namespace': {'a': 1},
        'proxy': {'b': 2},
        'user_list': [3],
        'user_string': 'text',
        'point': [1, 2],
    }
