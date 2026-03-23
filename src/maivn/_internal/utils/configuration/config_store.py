"""Configuration store for context-based configuration management.

This module provides the context-variable based configuration storage,
allowing configuration to be scoped to async contexts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .environment_config import MaivnConfiguration

# MARK: Configuration Store

_configuration_var: ContextVar[MaivnConfiguration | None] = ContextVar(
    "maivn_configuration",
    default=None,
)


def get_configuration() -> MaivnConfiguration:
    """Return the active configuration instance for the current context."""
    from .environment_config import MaivnConfiguration

    config = _configuration_var.get()
    if config is None:
        config = MaivnConfiguration()
        _configuration_var.set(config)
    return config


def set_configuration(config: MaivnConfiguration) -> None:
    """Set the configuration for the current context."""
    _configuration_var.set(config)


def reset_configuration() -> None:
    """Reset configuration to a fresh default instance for this context."""
    from .environment_config import MaivnConfiguration

    _configuration_var.set(MaivnConfiguration())


@contextmanager
def temporary_configuration(config: MaivnConfiguration) -> Iterator[MaivnConfiguration]:
    """Temporarily override configuration within a context.

    Args:
        config: Configuration to activate within the context.

    Yields:
        The configuration provided, for convenience.
    """
    token = _configuration_var.set(config)
    try:
        yield config
    finally:
        _configuration_var.reset(token)


# MARK: Exports

__all__ = [
    "get_configuration",
    "reset_configuration",
    "set_configuration",
    "temporary_configuration",
]
