"""Configuration helper accessors for internal application services."""

# pyright: strict
from __future__ import annotations

from ...utils.configuration import get_configuration

# MARK: Timeout Helpers


def get_default_timeout_seconds() -> float:
    """Resolve the current default execution timeout."""
    return get_configuration().execution.default_timeout_seconds


def get_pending_event_timeout_seconds() -> float:
    """Resolve the current pending-event timeout."""
    return get_configuration().execution.pending_event_timeout_seconds


# MARK: Public API

__all__ = ["get_default_timeout_seconds", "get_pending_event_timeout_seconds"]
