"""Configuration store for context-based configuration management.

This module provides the context-variable based configuration storage,
allowing configuration to be scoped to async contexts.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

# MaivnConfiguration stays lazy at runtime so direct imports of this store keep avoiding
# environment_config's import-time dotenv load until a default configuration is requested.
if TYPE_CHECKING:
    from .environment_config import MaivnConfiguration

# MARK: Configuration Store

_configuration_var: ContextVar[MaivnConfiguration | None] = ContextVar(
    "maivn_configuration",
    default=None,
)


def get_configuration() -> MaivnConfiguration:
    """Return the active configuration instance for the current context.

    The first read intentionally caches a default configuration in this context. That
    preserves the historical write-on-read behavior relied on by context restoration.
    """
    from .environment_config import MaivnConfiguration

    config = _configuration_var.get()
    if config is None:
        config = MaivnConfiguration()
        _ = _configuration_var.set(config)
    return config


def set_configuration(config: MaivnConfiguration) -> None:
    """Set the configuration for the current context."""
    _ = _configuration_var.set(config)


def reset_configuration() -> None:
    """Reset configuration to a fresh default instance for this context."""
    from .environment_config import MaivnConfiguration

    _ = _configuration_var.set(MaivnConfiguration())


@contextmanager
def temporary_configuration(
    config: MaivnConfiguration,
) -> Generator[MaivnConfiguration]:
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
