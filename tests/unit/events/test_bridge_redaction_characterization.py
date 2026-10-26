"""Characterization of private-data redaction edge cases on ``UIEvent`` payloads.

``test_bridge_private_data_redaction.py`` covers the main carriers. These tests
pin the remaining shapes: pydantic extra, excluded and computed fields, sets,
tuples, self-referencing lists, shared references, class objects and the
identity of untouched siblings.
"""

from __future__ import annotations

import dataclasses
import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, computed_field

from maivn import PrivateData
from maivn.events import UIEvent

RAW = '4111-1111-1111-1111'
OTHER_RAW = '123-45-6789'
DESCRIPTOR: dict[str, object] = {'__private__': True, 'label': 'Card', 'pii_type': 'credit_card'}
SSN_DESCRIPTOR: dict[str, object] = {'__private__': True, 'label': 'SSN', 'pii_type': 'ssn'}
COMPUTED_FAILURE = 'computed field failed'


def _card() -> PrivateData:
    return PrivateData(value=RAW, label='Card', pii_type='credit_card')


def _ssn() -> PrivateData:
    return PrivateData(value=OTHER_RAW, label='SSN', pii_type='ssn')


class _Extra(BaseModel):
    model_config = ConfigDict(extra='allow')

    note: str = 'kept'


class _Excluded(BaseModel):
    secret: PrivateData = Field(exclude=True)
    note: str = 'kept'


class _Computed(BaseModel):
    note: str = 'kept'

    @computed_field
    @property
    def card(self) -> PrivateData:
        """Return a private value from a computed field."""
        return _card()


class _Failing(BaseModel):
    holder: PrivateData

    @computed_field
    @property
    def broken(self) -> str:
        """Raise like a computed field whose dependency is missing."""
        raise RuntimeError(COMPUTED_FAILURE)


@dataclasses.dataclass
class _Record:
    card: PrivateData


def _sse_data(event: UIEvent) -> dict[str, object]:
    frame = event.to_sse()
    serialized = cast('str', frame['data'])
    assert RAW not in serialized
    assert OTHER_RAW not in serialized
    envelope = cast('dict[str, object]', json.loads(serialized))
    return cast('dict[str, object]', envelope['data'])


def test_extra_model_fields_are_redacted() -> None:
    """Values stored as pydantic extras are walked like declared fields."""
    model = _Extra.model_validate({'note': 'kept', 'card': _card()})
    event = UIEvent(type='final', data={'model': model})

    assert event.data == {'model': {'note': 'kept', 'card': DESCRIPTOR}}
    assert _sse_data(event) == {'model': {'note': 'kept', 'card': DESCRIPTOR}}


def test_excluded_model_fields_are_neither_walked_nor_serialized() -> None:
    """An excluded field is ignored by redaction because serialization drops it too."""
    model = _Excluded(secret=_card())
    event = UIEvent(type='final', data={'model': model})

    assert event.data['model'] is model
    assert _sse_data(event) == {'model': {'note': 'kept'}}


def test_excluded_field_does_not_hide_private_siblings() -> None:
    """Redacting a model with an excluded field drops that field from the rebuilt dict."""
    model = _Excluded(secret=_card())
    event = UIEvent(type='final', data={'model': model, 'ssn': _ssn()})

    assert event.data == {'model': model, 'ssn': SSN_DESCRIPTOR}


def test_computed_private_field_is_redacted() -> None:
    """A computed field returning PrivateData is replaced by its descriptor."""
    event = UIEvent(type='final', data={'model': _Computed()})

    assert event.data == {'model': {'note': 'kept', 'card': DESCRIPTOR}}
    assert _sse_data(event) == {'model': {'note': 'kept', 'card': DESCRIPTOR}}


def test_raising_computed_field_is_skipped_instead_of_failing_the_emit() -> None:
    """A computed field that raises is left out of the redacted model."""
    event = UIEvent(type='final', data={'model': _Failing(holder=_ssn())})

    assert event.data == {'model': {'holder': SSN_DESCRIPTOR}}


def test_sets_become_lists_and_tuples_stay_tuples() -> None:
    """Descriptors are unhashable, so redacted sets turn into lists; tuples keep type."""
    card = _card()
    event = UIEvent(
        type='final',
        data={
            # Frozen pydantic models hash at runtime; the stubs do not say so.
            'bag': frozenset({card, 'e', 'c', 'a', 'd', 'b'}),  # pyright: ignore[reportUnhashable]
            'pair': (card, 'kept'),
            'clean_pair': ('a', 'b'),
        },
    )

    # Sorted by ``repr``: quoted strings sort before the descriptor's ``{``.
    assert event.data['bag'] == ['a', 'b', 'c', 'd', 'e', DESCRIPTOR]
    assert event.data['pair'] == (DESCRIPTOR, 'kept')
    assert isinstance(event.data['pair'], tuple)
    assert event.data['clean_pair'] == ('a', 'b')


def test_self_referencing_list_breaks_the_cycle_with_none() -> None:
    """A cyclic list is rebuilt with ``None`` where it referenced itself."""
    items: list[object] = [_card()]
    items.append(items)

    event = UIEvent(type='final', data={'items': items})

    assert event.data == {'items': [DESCRIPTOR, None]}
    assert items[1] is items
    assert _sse_data(event) == {'items': [DESCRIPTOR, None]}


def test_shared_references_are_redacted_at_every_occurrence() -> None:
    """The same container referenced twice (without a cycle) is redacted both times."""
    shared: dict[str, object] = {'card': _card()}
    event = UIEvent(type='final', data={'first': shared, 'second': [shared]})

    assert event.data == {'first': {'card': DESCRIPTOR}, 'second': [{'card': DESCRIPTOR}]}


def test_untouched_siblings_and_class_objects_keep_their_identity() -> None:
    """Only containers that hold PrivateData are rebuilt."""
    plain_list: list[object] = [1, {'a': 2}]
    plain_dict: dict[str, object] = {'b': (3, 4)}
    event = UIEvent(
        type='final',
        data={'card': _card(), 'plain_list': plain_list, 'plain_dict': plain_dict, 'cls': _Record},
    )

    assert event.data['card'] == DESCRIPTOR
    assert event.data['plain_list'] is plain_list
    assert event.data['plain_dict'] is plain_dict
    assert event.data['cls'] is _Record


def test_dataclass_holding_private_data_inside_a_list_and_model() -> None:
    """Carriers nested across dataclass, list and model layers are all found."""
    record = _Record(card=_card())
    model = _Extra.model_validate({'records': [record]})
    event = UIEvent(type='final', data={'wrapped': model})

    assert event.data == {'wrapped': {'note': 'kept', 'records': [{'card': DESCRIPTOR}]}}
    assert record.card.value == RAW
