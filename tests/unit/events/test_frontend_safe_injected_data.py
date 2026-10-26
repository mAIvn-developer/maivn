"""A frontend-safe bridge never passes injected private values through (U05-D18, U05-D19)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from typing import cast

import pydantic
import pytest

from maivn.events import EventBridge, EventBridgeSecurityPolicy

PRIVATE_VALUE = 'secret-value-4111'
KNOWN_EVENT_TYPES = (
    'tool_event',
    'system_tool_complete',
    'system_tool_start',
    'system_tool_chunk',
    'agent_assignment',
    'final',
    'enrichment',
    'error',
    'assistant_chunk',
    'status_message',
    'status_message_chunk',
    'interrupt_required',
    'hook_fired',
    'session_start',
    'session_end',
)

_POLICY = EventBridgeSecurityPolicy(audience='frontend_safe')


def _sanitized(event_type: str, payload: dict[str, object]) -> str:
    return json.dumps(_POLICY.sanitize_event(event_type, payload), default=str)


@pytest.mark.parametrize('event_type', KNOWN_EVENT_TYPES)
@pytest.mark.parametrize('field', ['private_data_injected', 'interrupt_data_injected'])
def test_every_known_event_type_summarizes_injected_fields(event_type: str, field: str) -> None:
    """Known types without a dedicated handler are scrubbed like unknown types."""
    payload: dict[str, object] = {'params': {field: {'card': PRIVATE_VALUE}}, 'name': 'lookup'}

    sanitized = _POLICY.sanitize_event(event_type, payload)

    assert sanitized['params'] == {field: ['card']}
    assert sanitized['name'] == 'lookup'
    assert PRIVATE_VALUE not in json.dumps(sanitized)


@pytest.mark.parametrize(
    ('event_type', 'payload'),
    [
        ('tool_event', {'result': {'deep': {'private_data_injected': {'card': PRIVATE_VALUE}}}}),
        (
            'tool_event',
            {'tool': {'args': {'x': [{'private_data_injected': {'card': PRIVATE_VALUE}}]}}},
        ),
        (
            'final',
            {'output': {'result': {'a': {'private_data_injected': {'card': PRIVATE_VALUE}}}}},
        ),
        (
            'agent_assignment',
            {'assignment': {'result': {'a': {'interrupt_data_injected': {'card': PRIVATE_VALUE}}}}},
        ),
        (
            'system_tool_complete',
            {'tool': {'result': {'a': {'b': {'private_data_injected': {'card': PRIVATE_VALUE}}}}}},
        ),
    ],
)
def test_injected_fields_are_summarized_at_any_depth(
    event_type: str,
    payload: dict[str, object],
) -> None:
    """Nesting below args or result does not hide an injected field."""
    sanitized = _sanitized(event_type, payload)

    assert PRIVATE_VALUE not in sanitized
    assert '["card"]' in sanitized


def test_list_summaries_show_only_item_types() -> None:
    """A list cannot tell names from values, so even string items are reduced to a type."""
    payload: dict[str, object] = {
        'private_data_injected': [PRIVATE_VALUE, {'card': PRIVATE_VALUE}, 42],
    }

    sanitized = _POLICY.sanitize_event('status_message', payload)

    assert sanitized == {'private_data_injected': ['str', 'dict', 'int']}


def test_a_very_deep_payload_is_redacted_below_the_depth_limit_instead_of_crashing() -> None:
    """Nesting far beyond any real payload cannot exhaust the recursion limit."""
    payload: dict[str, object] = {'private_data_injected': {'card': PRIVATE_VALUE}}
    for _ in range(5000):
        payload = {'next': payload}

    sanitized = _sanitized('status_message', payload)

    assert PRIVATE_VALUE not in sanitized
    assert '"<redacted>"' in sanitized


def test_a_cyclic_payload_is_cut_and_never_links_back_to_unscrubbed_data() -> None:
    """A cycle is replaced by a marker, so no path leads back to the original container."""
    inner: dict[str, object] = {'private_data_injected': {'card': PRIVATE_VALUE}}
    inner['self'] = inner
    payload: dict[str, object] = {'inner': inner}

    sanitized = _POLICY.sanitize_event('status_message', payload)

    scrubbed_inner = sanitized['inner']
    assert isinstance(scrubbed_inner, dict)
    assert scrubbed_inner['private_data_injected'] == ['card']
    assert scrubbed_inner['self'] == '<redacted>'


def test_an_internal_bridge_keeps_the_payload_untouched() -> None:
    """Only frontend-safe audiences are scrubbed."""
    payload: dict[str, object] = {'params': {'private_data_injected': {'card': PRIVATE_VALUE}}}

    assert EventBridgeSecurityPolicy().sanitize_event('tool_event', payload) is payload


def test_a_frontend_safe_bridge_keeps_injected_values_out_of_history_and_sse() -> None:
    """End to end: emitted system tool parameters reach neither history nor the frame."""
    bridge = EventBridge('ses-1', audience='frontend_safe')

    asyncio.run(
        bridge.emit_system_tool_start(
            'lookup',
            'tl-1',
            params={'private_data_injected': {'card': PRIVATE_VALUE}},
        ),
    )

    history = bridge.get_history()
    assert history
    assert PRIVATE_VALUE not in json.dumps(history, default=str)


@dataclasses.dataclass
class _Carrier:
    private_data_injected: dict[str, str]
    label: str


class _CarrierModel(pydantic.BaseModel):
    private_data_injected: dict[str, str]
    label: str


@pytest.mark.parametrize(
    'container',
    [
        ({'private_data_injected': {'card': PRIVATE_VALUE}},),
        _Carrier(private_data_injected={'card': PRIVATE_VALUE}, label='x'),
        _CarrierModel(private_data_injected={'card': PRIVATE_VALUE}, label='x'),
    ],
    ids=['tuple', 'dataclass', 'pydantic'],
)
def test_tuples_dataclasses_and_models_are_scrubbed_too(container: object) -> None:
    """In-process callers may hand the bridge non-JSON containers; they are walked as well."""
    sanitized = _sanitized('tool_event', {'result': {'value': container}})

    assert PRIVATE_VALUE not in sanitized
    assert '["card"]' in sanitized


@pytest.mark.parametrize(
    'key', ['Private_Data_Injected', ' private_data_injected ', 'INTERRUPT_DATA_INJECTED']
)
def test_injected_field_names_match_regardless_of_case_and_padding(key: str) -> None:
    """A field that differs only in case or surrounding spaces is still summarized."""
    sanitized = _POLICY.sanitize_event('status_message', {'detail': {key: {'card': PRIVATE_VALUE}}})

    assert sanitized == {'detail': {key: ['card']}}


def test_shared_sub_objects_are_walked_once() -> None:
    """A payload that reuses one container at every level stays linear to scrub."""
    shared: object = {'private_data_injected': {'card': PRIVATE_VALUE}}
    for _ in range(60):
        shared = [shared, shared]

    started = time.perf_counter()
    sanitized = _POLICY.sanitize_event('status_message', {'tree': shared})
    elapsed = time.perf_counter() - started

    node = sanitized['tree']
    while isinstance(node, list):
        pair = cast('list[object]', node)
        assert pair[0] is pair[1]
        node = pair[0]
    assert node == {'private_data_injected': ['card']}
    assert elapsed < 1.0


def test_a_scrubbed_tuple_stays_a_tuple() -> None:
    """Only the injected field changes; the container keeps its type."""
    sanitized = _POLICY.sanitize_event(
        'status_message',
        {'items': ({'private_data_injected': {'card': PRIVATE_VALUE}}, 'kept')},
    )

    assert sanitized == {'items': ({'private_data_injected': ['card']}, 'kept')}
