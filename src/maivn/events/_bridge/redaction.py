"""Private-data redaction for everything that leaves the event bridge.

``maivn.PrivateData`` carries a RAW private value. Event payloads may hold one
at any depth, and every bridge output - live SSE frames, history, snapshots,
replay and ``on_packet`` observers - reads the payload stored on
:class:`~maivn.events.UIEvent`. :func:`redact_private_data` swaps each
``PrivateData`` for a descriptor that never includes ``value``, for every
audience. The descriptor has the same shape the Booth boundary emits.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import itertools
from collections import UserString, deque
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypeVar, cast

from pydantic import BaseModel, RootModel

from maivn._internal.compat.privacy import (
    PRIVATE_DESCRIPTOR_KEY,
    PrivateData,
    private_data_descriptor,
    private_data_descriptors_only,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


_T = TypeVar('_T')

# Core-schema serializer kinds that call a function.
_SERIALIZER_FUNCTIONS = frozenset({'function-plain', 'function-wrap'})

# Sequences that cannot hold a PrivateData, or (a range) may be too large to walk.
_NOT_WALKED = (str, bytes, bytearray, memoryview, range, UserString)

# Containers whose items are already in memory. Any other container may be virtual and
# endless, and ``list()`` trusts its ``__len__`` (Linux overcommit lets a 10**12-slot
# preallocation succeed), so it is read up to ``_MAX_VIRTUAL_ITEMS`` items.
_IN_MEMORY = frozenset({list, tuple, dict, set, frozenset, deque})
_MAX_VIRTUAL_ITEMS = 100_000


# MARK: Redaction


def redact_private_data(value: object) -> object:
    """Return ``value`` with every nested ``PrivateData`` replaced by its descriptor.

    Walks mappings, sequences, sets, namespaces, dataclass instances and pydantic
    models at any depth. When nothing inside is ``PrivateData`` the same object
    comes back, so every other payload keeps its identity and serialization. When
    something is, the affected containers are rebuilt and the caller's objects
    are never mutated: a pydantic model or dataclass becomes a dict of its walked
    fields, or its type name when it has serializer code (see ``_redact_pydantic``);
    a plain dataclass, namespace or mapping becomes a dict, a set a sorted list and
    any other sequence but a tuple a list. Iterators are never walked: consuming one
    would change the payload. A container that cannot be read is left as it is, and
    the serializer emits it by type name.
    """
    if not _contains_private_data(value):
        return value
    return _redact(value, set())


def _contains_private_data(value: object) -> bool:
    """Find declared carriers with an iterative walk, including cyclic/deep containers."""
    pending = [value]
    visited: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, PrivateData):
            return True
        if id(item) in visited:
            continue
        children = _children(item)
        if children is None:
            continue
        visited.add(id(item))
        pending.extend(children)
    return False


def _redact(value: object, active_ids: set[int]) -> object:
    if isinstance(value, PrivateData):
        return private_data_descriptor(value)
    if _children(value) is None:
        return value

    # A cycle must not retain the original container holding a raw declared value.
    # This walk runs only when a PrivateData carrier exists; plain payloads stay intact.
    value_id = id(value)
    if value_id in active_ids:
        return None
    active_ids.add(value_id)
    try:
        redacted = _redact_container(value, active_ids)
    finally:
        active_ids.remove(value_id)
    return value if redacted is None else redacted


def _redact_container(value: object, active_ids: set[int]) -> object | None:
    """Return a redacted copy of ``value``, or ``None`` when it holds no PrivateData."""
    fields = _fields(value)
    if fields is not None:
        redacted = {key: _redact(item, active_ids) for key, item in fields.items()}
        if not _changed(fields.values(), redacted.values()):
            return None
        if not _is_pydantic(value):
            return redacted
        return _redact_pydantic(value, fields, redacted)

    items = _children(value)
    if items is None:
        return None
    redacted_items = [_redact(item, active_ids) for item in items]
    if not _changed(items, redacted_items):
        return None
    if isinstance(value, tuple):
        return tuple(redacted_items)
    if isinstance(value, AbstractSet):
        # Descriptors are unhashable; the serializer emits sets as sorted lists anyway.
        return sorted(redacted_items, key=repr)
    return redacted_items


def _redact_pydantic(
    value: object,
    fields: Mapping[object, object],
    redacted: dict[object, object],
) -> object:
    """Rebuild a pydantic model or dataclass that holds a visible ``PrivateData``.

    Without serializer code it becomes its walked fields, as any dataclass does. With
    serializer code - a field or model serializer, an ``Annotated`` serializer, or a
    computed field other than one returning a ``PrivateData`` - it is emitted by type
    name: that code could reach the value through ``self`` and hand it out in a form no
    walk can recognize, and skipping it could expose what a mask was hiding.
    """
    model_type = type(value)
    carrier_keys = {key for key, item in fields.items() if redacted[key] is not item}
    computed = set(getattr(model_type, '__pydantic_computed_fields__', {})) & set(fields)
    if (computed - carrier_keys) or _has_serialization_code(model_type):
        return model_type.__name__
    return redacted['root'] if isinstance(value, RootModel) else redacted


def _is_authored_serializer(serialization: object) -> bool:
    """Return True for a function serializer written outside pydantic itself."""
    if not isinstance(serialization, dict):
        return False
    entries = cast('dict[str, object]', serialization)
    if entries.get('type') not in _SERIALIZER_FUNCTIONS:
        return False
    module = getattr(entries.get('function'), '__module__', None) or ''
    return not module.startswith(('pydantic.', 'pydantic_core.'))


@functools.lru_cache(maxsize=256)
def _has_serialization_code(model_type: type) -> bool:
    """Return True when a model or pydantic dataclass carries serializer code anywhere.

    Reads the compiled core schema, so decorators, ``Annotated`` serializers and types
    that serialize themselves (``__get_pydantic_core_schema__``) all count, at any depth.
    ``PrivateData``'s own value-free serializers do not. A schema that is not built yet
    counts as code.
    """
    schema = cast('object', getattr(model_type, '__pydantic_core_schema__', None))
    if not isinstance(schema, dict):
        return True
    pending: list[object] = [schema]
    visited: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, list | tuple):
            pending.extend(cast('Iterable[object]', node))
            continue
        if not isinstance(node, dict):
            continue
        entries = cast('dict[str, object]', node)
        owner = entries.get('cls')
        if isinstance(owner, type) and issubclass(owner, PrivateData):
            continue
        if _is_authored_serializer(entries.get('serialization')):
            return True
        pending.extend(entries.values())
    return False


def _changed(before: Iterable[object], after: Iterable[object]) -> bool:
    return any(new is not old for new, old in zip(after, before, strict=True))


def _is_pydantic(value: object) -> bool:
    """Return True for a pydantic model or pydantic dataclass instance."""
    return isinstance(value, BaseModel) or (
        not isinstance(value, type) and hasattr(type(value), '__pydantic_fields__')
    )


def _model_fields(model: BaseModel) -> dict[str, object]:
    """Return the values ``model_dump()`` would serialize, keyed by field name."""
    model_type = type(model)
    fields: dict[str, object] = {
        name: getattr(model, name)
        for name, info in model_type.model_fields.items()
        if not info.exclude
    }
    fields.update(model.__pydantic_extra__ or {})
    for name in model_type.model_computed_fields:
        # A computed field that raises would fail serialization too; skip it here
        # rather than fail the emit.
        with contextlib.suppress(Exception):
            fields[name] = getattr(model, name)
    return fields


def dataclass_fields(value: object) -> dict[str, object]:
    if not dataclasses.is_dataclass(value):
        return {}
    return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}


def _fields(value: object) -> Mapping[object, object] | None:
    """Return the keyed values of a mapping, model, namespace or dataclass, else None."""
    if isinstance(value, Mapping):
        return cast('Mapping[object, object]', value)
    if isinstance(value, BaseModel):
        return cast('dict[object, object]', _model_fields(value))
    if isinstance(value, SimpleNamespace):
        return cast('dict[object, object]', vars(value))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return cast('dict[object, object]', dataclass_fields(value))
    return None


def _children(value: object) -> list[object] | None:
    """Return the values a container holds, or None for a leaf.

    A container whose items cannot be read (one that raises, or one too large to
    hold in memory) is a leaf: the walk must never fail the emit.
    """
    try:
        fields = _fields(value)
        if fields is not None:
            return bounded_items(fields, fields.values())
        if isinstance(value, Sequence | AbstractSet) and not isinstance(value, _NOT_WALKED):
            items = cast('Iterable[object]', value)
            return bounded_items(items, items)
    except Exception:  # noqa: BLE001 - an unreadable container is emitted by type name.
        return None
    return None


def bounded_items(container: object, items: Iterable[_T]) -> list[_T]:
    """Return ``items`` as a list, refusing a custom container past the walk limit.

    Raises OverflowError when ``container`` is not a built-in in-memory container and
    holds more than ``_MAX_VIRTUAL_ITEMS`` items; callers treat it as unreadable.
    """
    if type(container) in _IN_MEMORY:
        return list(items)
    walked = list(itertools.islice(items, _MAX_VIRTUAL_ITEMS + 1))
    if len(walked) > _MAX_VIRTUAL_ITEMS:
        message = f'{type(container).__name__} holds more than {_MAX_VIRTUAL_ITEMS} items'
        raise OverflowError(message)
    return walked


__all__ = [
    'PRIVATE_DESCRIPTOR_KEY',
    'bounded_items',
    'private_data_descriptors_only',
    'dataclass_fields',
    'private_data_descriptor',
    'redact_private_data',
]
