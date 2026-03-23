"""Configuration helper accessors for internal application services."""

from __future__ import annotations

from ...utils.configuration import get_configuration


def get_default_timeout_seconds() -> float:
    """Resolve the current default execution timeout."""
    return get_configuration().execution.default_timeout_seconds


def get_pending_event_timeout_seconds() -> float:
    """Resolve the current pending-event timeout."""
    return get_configuration().execution.pending_event_timeout_seconds


__all__ = ["get_default_timeout_seconds", "get_pending_event_timeout_seconds"]
