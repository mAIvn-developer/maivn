"""UUID generation utilities for maivn-core.

This module provides utilities for generating unique identifiers,
supporting both random and deterministic UUID generation.
"""

from __future__ import annotations

import uuid

# MARK: - UUID Utilities


def create_uuid(obj: object | None = None) -> str:
    """Create a unique identifier as a string.

    If an object is provided, generate a deterministic UUID v5 using the object's
    intrinsic properties (module + qualname for functions/classes, or repr for others).
    Otherwise, return a random UUID v4.

    Args:
        obj: Optional object to generate deterministic UUID from

    Returns:
        UUID string (deterministic if obj provided, random otherwise)

    """
    if obj is None:
        return str(uuid.uuid4())

    identifier = _get_deterministic_identifier(obj)
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, identifier))


# MARK: - Helper Functions


def _get_deterministic_identifier(obj: object) -> str:
    """Get a deterministic identifier string for an object.

    For functions and classes, uses module + qualname for consistency across runs.
    For other objects, falls back to type name + repr.

    Args:
        obj: Object to generate identifier for

    Returns:
        Deterministic identifier string

    """
    if callable(obj) or isinstance(obj, type):
        identifier = _get_callable_identifier(obj)
        if identifier:
            return identifier

    return _get_fallback_identifier(obj)


def _get_callable_identifier(obj: object) -> str | None:
    """Get identifier for callable objects using module and qualname.

    Args:
        obj: Callable object or type

    Returns:
        Identifier string or None if not determinable

    """
    module = getattr(obj, '__module__', None)
    if not module:
        return None

    qualname = getattr(obj, '__qualname__', None)
    if qualname:
        return f'{module}.{qualname}'

    name = getattr(obj, '__name__', None)
    if name:
        return f'{module}.{name}'

    return None


def _get_fallback_identifier(obj: object) -> str:
    """Get fallback identifier using type and repr.

    Args:
        obj: Object to generate identifier for

    Returns:
        Fallback identifier string

    """
    type_name = type(obj).__name__
    try:
        return f'{type_name}:{obj!r}'
    except Exception:  # noqa: BLE001 - repr(obj) may raise; id fallback preserves UUIDs.
        return f'{type_name}:{id(obj)}'
