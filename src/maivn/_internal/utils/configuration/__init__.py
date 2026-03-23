"""Configuration management for maivn internals.
Provides centralized configuration objects and access helpers.
Use ``ConfigurationBuilder`` to build environment-specific configuration.
"""

from __future__ import annotations

# MARK: - Imports
from .config_builder import ConfigurationBuilder
from .config_store import (
    get_configuration,
    reset_configuration,
    set_configuration,
    temporary_configuration,
)
from .environment_config import (
    ExecutionConfiguration,
    LoggingConfiguration,
    MaivnConfiguration,
    SecurityConfiguration,
    ServerConfiguration,
)

# MARK: - Exports

__all__ = [
    # Configuration classes
    "ExecutionConfiguration",
    "LoggingConfiguration",
    "MaivnConfiguration",
    "SecurityConfiguration",
    "ServerConfiguration",
    # Configuration functions
    "get_configuration",
    "reset_configuration",
    "set_configuration",
    "temporary_configuration",
    # Builder
    "ConfigurationBuilder",
]
