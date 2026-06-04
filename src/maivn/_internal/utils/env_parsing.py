"""Environment variable parsing helpers for maivn internals."""

# pyright: strict
from __future__ import annotations

import os
from typing import Final

# MARK: - Boolean Tokens

_BOOL_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE_VALUES: Final[frozenset[str]] = frozenset({"", "0", "false", "no", "off"})

# MARK: - Raw Access


def get_env(name: str) -> str | None:
    """Return the raw environment value for a given name."""
    return os.getenv(name)


def coerce_str_env(name: str) -> str | None:
    """Return a stripped string value, or ``None`` when unset or empty.

    An empty/whitespace-only value (``KEY=``) is treated as unset so callers
    fall back to their default instead of receiving an empty string.
    """
    raw = get_env(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


# MARK: - Coercion Helpers


def coerce_bool_env(name: str) -> bool | str | None:
    """Return boolean value when possible, else raw string/None.

    ``maivn_shared.utils.env.get_env_bool`` treats any non-falsy string as true.
    These SDK helpers keep an explicit allow-list so typos do not silently enable
    flags. Empty strings are explicit false values for both bool env paths.
    """
    raw = get_env(name)
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in _BOOL_TRUE_VALUES:
        return True
    if lowered in _BOOL_FALSE_VALUES:
        return False
    return raw


def coerce_bool_value(value: object, *, default: bool = False) -> bool:
    """Coerce an in-hand value to bool using the canonical token vocabulary.

    Companion to :func:`coerce_bool_env` for values already in hand (for
    example, pulled from a config mapping) rather than environment variable
    names. Strings are matched case-insensitively against the canonical
    true/false token sets; unrecognized strings fall back to ``default``.
    Non-string values use standard truthiness.
    """
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _BOOL_TRUE_VALUES:
            return True
        if token in _BOOL_FALSE_VALUES:
            return False
        return default
    return bool(value)


def coerce_int_env(name: str) -> int | str | None:
    """Return int value when possible, else raw string/None.

    An unset variable or an empty/whitespace-only value (``KEY=``) is treated
    as unset and returns ``None`` so callers fall back to their default.
    """
    raw = get_env(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return raw


def coerce_float_env(name: str) -> float | str | None:
    """Return float value when possible, else raw string/None.

    An unset variable or an empty/whitespace-only value (``KEY=``) is treated
    as unset and returns ``None`` so callers fall back to their default.
    """
    raw = get_env(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return raw


# MARK: - Defaulted Readers


def read_bool_env(name: str, *, default: bool) -> bool:
    """Return a boolean from env or fallback to default when invalid."""
    raw = get_env(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _BOOL_TRUE_VALUES:
        return True
    if value in _BOOL_FALSE_VALUES:
        return False
    return default


def read_int_env(name: str, *, default: int) -> int:
    """Return an int from env or fallback to default when invalid."""
    raw = get_env(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def read_str_env(name: str, *, default: str) -> str:
    """Return a string from env or fallback to default when missing."""
    raw = get_env(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


__all__ = [
    "coerce_bool_env",
    "coerce_bool_value",
    "coerce_float_env",
    "coerce_int_env",
    "coerce_str_env",
    "get_env",
    "read_bool_env",
    "read_int_env",
    "read_str_env",
]
