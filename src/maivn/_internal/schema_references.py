"""Remove unreachable Pydantic definitions after dependency argument projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from maivn._internal.models import JsonObject

_DEFINITION_PREFIX = '#/$defs/'
_SCOPED_REFERENCE_KEYS = frozenset({'$id', '$anchor', '$dynamicAnchor', '$dynamicRef', '$defs'})


def prune_unused_definitions(schema: JsonObject) -> JsonObject:
    """Preserve reachable shared/recursive definitions; leave unfamiliar scopes intact."""
    raw_definitions = schema.get('$defs')
    if not isinstance(raw_definitions, Mapping):
        return schema
    definitions = cast('Mapping[str, object]', raw_definitions)
    root = {key: value for key, value in schema.items() if key != '$defs'}
    references = _definition_references(root)
    if references is None:
        return schema
    pending = list(references)
    reachable: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        if name not in definitions:
            return schema
        reachable.add(name)
        nested = _definition_references(definitions[name])
        if nested is None:
            return schema
        pending.extend(nested - reachable)
    if reachable:
        root['$defs'] = {name: value for name, value in definitions.items() if name in reachable}
    return root


def _definition_references(value: object) -> set[str] | None:
    """Collect local root pointers, declining anchors, external refs and nested scopes."""
    references: set[str] = set()
    pending: list[object] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            mapping = cast('Mapping[str, object]', item)
            if _SCOPED_REFERENCE_KEYS.intersection(mapping):
                return None
            if '$ref' in mapping:
                reference = mapping['$ref']
                if not isinstance(reference, str) or not reference.startswith(_DEFINITION_PREFIX):
                    return None
                name = reference[len(_DEFINITION_PREFIX) :].split('/', 1)[0]
                references.add(name.replace('~1', '/').replace('~0', '~'))
            pending.extend(mapping.values())
        elif isinstance(item, list):
            pending.extend(cast('list[object]', item))
    return references
