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
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, RootModel

from maivn._internal.compat.privacy import PrivateData

if TYPE_CHECKING:
    from collections.abc import Iterable

# MARK: Descriptor

PRIVATE_DESCRIPTOR_KEY = '__private__'


def private_data_descriptor(item: PrivateData) -> dict[str, object]:
    """Return a displayable descriptor for ``item``. NEVER includes ``value``."""
    return {
        PRIVATE_DESCRIPTOR_KEY: True,
        'label': item.label or item.name or item.pii_type or 'private data',
        'pii_type': item.pii_type,
    }


# MARK: Redaction


def redact_private_data(value: object) -> object:
    """Return ``value`` with every nested ``PrivateData`` replaced by its descriptor.

    Walks dicts, lists, tuples, sets, dataclass instances and pydantic models
    at any depth. When nothing inside is ``PrivateData`` the same object comes
    back, so every other payload keeps its identity and serialization. When
    something is, the affected containers are rebuilt and the caller's objects
    are never mutated: a dataclass or model becomes a dict of its fields (the
    shape the SSE serializer gives it) and a set becomes a list.
    """
    return _redact(value, set())


def _redact(value: object, active_ids: set[int]) -> object:
    if isinstance(value, PrivateData):
        return private_data_descriptor(value)
    if not _is_container(value):
        return value

    # A container already on the walk path is a cycle; leave it as it is.
    value_id = id(value)
    if value_id in active_ids:
        return value
    active_ids.add(value_id)
    try:
        redacted = _redact_container(value, active_ids)
    finally:
        active_ids.remove(value_id)
    return value if redacted is None else redacted


def _redact_container(value: object, active_ids: set[int]) -> object | None:
    """Return a redacted copy of ``value``, or ``None`` when it holds no PrivateData."""
    if isinstance(value, dict):
        mapping = cast('dict[object, object]', value)
        redacted = {key: _redact(item, active_ids) for key, item in mapping.items()}
        return redacted if _changed(mapping.values(), redacted.values()) else None

    if isinstance(value, list | tuple | set | frozenset):
        items = list(cast('Iterable[object]', value))
        redacted_items = [_redact(item, active_ids) for item in items]
        if not _changed(items, redacted_items):
            return None
        if isinstance(value, list):
            return redacted_items
        if isinstance(value, tuple):
            return tuple(redacted_items)
        # Descriptors are unhashable; the serializer emits sets as sorted lists anyway.
        return sorted(redacted_items, key=repr)

    if isinstance(value, RootModel):
        # ``model_dump()`` of a root model is its root value, not a field dict.
        root = cast('object', value.root)
        redacted_root = _redact(root, active_ids)
        return None if redacted_root is root else redacted_root

    fields = _model_fields(value) if isinstance(value, BaseModel) else _dataclass_fields(value)
    redacted_fields = {name: _redact(item, active_ids) for name, item in fields.items()}
    return redacted_fields if _changed(fields.values(), redacted_fields.values()) else None


def _changed(before: Iterable[object], after: Iterable[object]) -> bool:
    return any(new is not old for new, old in zip(after, before, strict=True))


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


def _dataclass_fields(value: object) -> dict[str, object]:
    if not dataclasses.is_dataclass(value):
        return {}
    return {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}


def _is_container(value: object) -> bool:
    if isinstance(value, dict | list | tuple | set | frozenset | BaseModel):
        return True
    return not isinstance(value, type) and dataclasses.is_dataclass(value)


__all__ = [
    'PRIVATE_DESCRIPTOR_KEY',
    'private_data_descriptor',
    'redact_private_data',
]
