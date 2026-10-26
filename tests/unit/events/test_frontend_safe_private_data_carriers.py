"""Scrubbing injected fields never strips the PrivateData redaction that follows it.

A frontend-safe payload is scrubbed, then serialized; serialization replaces every
``PrivateData`` with a value-free descriptor. The scrub must therefore never turn a
``PrivateData`` (or a model holding one) into a plain dict.
"""

from __future__ import annotations

import collections
import dataclasses
import types

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_serializer

from maivn import PrivateData
from maivn.events import EventBridgeSecurityPolicy, UIEvent

RAW = '123-45-6789'
_POLICY = EventBridgeSecurityPolicy(audience='frontend_safe')


def _frame(event_type: str, payload: dict[str, object]) -> str:
    sanitized = _POLICY.sanitize_event(event_type, payload)
    return str(UIEvent(type=event_type, data=sanitized, id='evt-1', timestamp='t').to_sse()['data'])


class _ToolResult(BaseModel):
    private_data_injected: dict[str, str]
    customer: PrivateData


@dataclasses.dataclass
class _Carrier:
    private_data_injected: dict[str, str]
    customer: PrivateData


def _deep(levels: int, leaf: object) -> object:
    node = leaf
    for _ in range(levels):
        node = {'next': node}
    return node


def _cyclic_value() -> dict[str, object]:
    value: dict[str, object] = {'ssn': RAW}
    value['self'] = value
    return value


@pytest.mark.parametrize(
    'payload',
    [
        {
            'tool': {
                'result': _ToolResult(
                    private_data_injected={'k': 'v'}, customer=PrivateData(value=RAW)
                )
            }
        },
        {
            'tool': {
                'result': _Carrier(
                    private_data_injected={'k': 'v'}, customer=PrivateData(value=RAW)
                )
            }
        },
        {'tool': {'result': PrivateData(value=_cyclic_value())}},
        {'tool': {'result': PrivateData(value={'ssn': RAW, 'private_data_injected': {'k': 'v'}})}},
        {'tool': {'result': _deep(59, PrivateData(value=_deep(10, RAW)))}},
    ],
    ids=[
        'model-with-injected-field',
        'dataclass-with-injected-field',
        'cyclic-value',
        'injected-inside-value',
        'value-across-depth-cut',
    ],
)
def test_private_data_is_never_turned_into_a_plain_dict(payload: dict[str, object]) -> None:
    """The raw value stays behind the descriptor in the frontend-safe frame."""
    frame = _frame('tool_event', payload)

    assert RAW not in frame
    assert '"__private__":true' in frame.replace(' ', '')


@pytest.mark.parametrize(
    'container',
    [
        types.MappingProxyType({'private_data_injected': {'ssn': RAW}}),
        collections.UserDict({'private_data_injected': {'ssn': RAW}}),
        types.SimpleNamespace(private_data_injected={'ssn': RAW}),
        RootModel[dict[str, object]]({'private_data_injected': {'ssn': RAW}}),
    ],
    ids=['mappingproxy', 'userdict', 'simplenamespace', 'rootmodel'],
)
def test_other_containers_are_walked(container: object) -> None:
    """Mappings, namespaces and root models do not hide an injected field."""
    frame = _frame('final', {'output': container})

    assert RAW not in frame


def test_a_root_model_of_a_scalar_passes_through() -> None:
    """A root model with nothing to scrub is returned as it is, never raising."""
    value = RootModel[int](7)

    assert _POLICY.sanitize_event('final', {'output': value}) == {'output': value}


def test_a_root_model_is_scrubbed_as_its_root_value() -> None:
    """The serializer emits a root model as its root, so the scrub returns that shape."""
    model = RootModel[dict[str, object]]({'private_data_injected': {'ssn': RAW}, 'kept': 1})

    sanitized = _POLICY.sanitize_event('final', {'output': model})

    assert sanitized == {'output': {'private_data_injected': ['ssn'], 'kept': 1}}


def test_a_rebuilt_dataclass_keeps_its_untouched_fields_by_identity() -> None:
    """Fields are read, not copied: only the injected field is replaced."""
    customer = PrivateData(value=RAW)
    carrier = _Carrier(private_data_injected={'k': 'v'}, customer=customer)

    sanitized = _POLICY.sanitize_event('final', {'output': carrier})

    output = sanitized['output']
    assert isinstance(output, dict)
    assert output['private_data_injected'] == ['k']
    assert output['customer'] is customer


class _Masked(BaseModel):
    """A model that masks one field when serialized and carries an injected field."""

    ssn: str
    meta: dict[str, object]

    @field_serializer('ssn')
    def _mask(self, value: str) -> str:
        return '***' if value else value


class _Aliased(BaseModel):
    """A model whose serialized key for ``payload`` is an injected-field name."""

    model_config = ConfigDict(serialize_by_alias=True)

    payload: dict[str, object] = Field(serialization_alias='private_data_injected')


def test_a_rebuilt_model_keeps_its_field_serializers() -> None:
    """Masking serializers still apply after an injected field is summarized."""
    frame = _frame('final', {'output': _Masked(ssn=RAW, meta={'private_data_injected': {'k': 1}})})

    assert RAW not in frame
    assert '"ssn":"***"' in frame.replace(' ', '')


def test_an_injected_field_name_reached_through_an_alias_is_summarized() -> None:
    """The scrub sees the keys the serializer will emit, not only field names."""
    frame = _frame('final', {'output': _Aliased(payload={'k': RAW})})

    assert RAW not in frame
