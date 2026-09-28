"""Pruning preserves JSON Pointer resolution and declines unfamiliar reference scopes."""

from copy import deepcopy

import pytest

from maivn._internal.schema_references import prune_unused_definitions


@pytest.mark.parametrize(
    'reference', ['#named', 'https://example.com/schema', '#/properties/value']
)
def test_unfamiliar_reference_preserves_definitions(reference: str) -> None:
    """Do not infer reachability for external or non-definition pointers."""
    schema = {
        'type': 'object',
        'properties': {'value': {'$ref': reference}},
        '$defs': {'Named': {'$anchor': 'named', 'type': 'string'}},
    }
    assert prune_unused_definitions(schema) == schema


def test_escaped_pointer_and_transitive_cycle_are_preserved() -> None:
    """Names are decoded as JSON Pointers and cycles terminate without losing links."""
    schema = {
        'type': 'object',
        'properties': {'value': {'$ref': '#/$defs/A~1B~0C'}},
        '$defs': {
            'A/B~C': {'type': 'object', 'properties': {'next': {'$ref': '#/$defs/Child'}}},
            'Child': {'type': 'object', 'properties': {'parent': {'$ref': '#/$defs/A~1B~0C'}}},
            'Unused': {'type': 'number'},
        },
    }
    original = deepcopy(schema)
    pruned = prune_unused_definitions(schema)
    assert set(pruned['$defs']) == {'A/B~C', 'Child'}
    assert schema == original


def test_nested_scope_keeps_original_definitions() -> None:
    """A nested schema resource can change what a local pointer means."""
    schema = {
        'type': 'object',
        'properties': {'value': {'$ref': '#/$defs/Scoped'}},
        '$defs': {'Scoped': {'$id': 'nested', 'type': 'string'}, 'Other': {'type': 'number'}},
    }
    assert prune_unused_definitions(schema) == schema
