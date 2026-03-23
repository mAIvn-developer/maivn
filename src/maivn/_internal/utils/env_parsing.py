"""Environment variable parsing helpers for maivn internals."""

from __future__ import annotations

import os

# MARK: - Raw Access


def get_env(name: str) -> str | None:
    """Return the raw environment value for a given name."""
    return os.getenv(name)


# MARK: - Coercion Helpers (preserve invalid values)


def coerce_bool_env(name: str) -> bool | str | None:
    """Return boolean value when possible, else raw string/None."""
    raw = get_env(name)
    if raw is None:
        return None
    lowered = raw.strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no", ""}:
        return False
    return raw


def coerce_int_env(name: str) -> int | str | None:
    """Return int value when possible, else raw string/None."""
    raw = get_env(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def coerce_float_env(name: str) -> float | str | None:
    """Return float value when possible, else raw string/None."""
    raw = get_env(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return raw


# MARK: - Defaulted Readers


def read_bool_env(name: str, *, default: bool) -> bool:
    """Return a boolean from env or fallback to default when invalid."""
    raw = get_env(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
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
    "coerce_float_env",
    "coerce_int_env",
    "get_env",
    "read_bool_env",
    "read_int_env",
    "read_str_env",
]
