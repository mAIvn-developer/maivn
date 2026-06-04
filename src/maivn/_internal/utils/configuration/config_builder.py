"""Configuration builder for convenient configuration creation.

This module provides convenience methods for creating configuration from
environment variables. ``environment_config`` preserves the SDK's historical
``.env`` import behavior for the default server URL; this builder centralizes
the remaining environment-driven configuration.
"""

# pyright: strict
from __future__ import annotations

from typing import TypeAlias, cast

from maivn_shared.utils.env import remove_none_values
from pydantic import JsonValue

from ..env_parsing import (
    coerce_bool_env,
    coerce_float_env,
    coerce_int_env,
    coerce_str_env,
)
from .environment_config import ConfigMapping, MaivnConfiguration
from .keys import (
    FIELD_API_KEY,
    FIELD_DEFAULT_TIMEOUT_SECONDS,
    FIELD_DEPENDENCY_WAIT_TIMEOUT_SECONDS,
    FIELD_DEPLOYMENT_TIMEZONE,
    FIELD_ENABLE_BACKGROUND_EXECUTION,
    FIELD_ENABLE_TIMING_LOGS,
    FIELD_FORMAT_STRING,
    FIELD_LOG_LEVEL,
    FIELD_MAX_PARALLEL_TOOLS,
    FIELD_MAX_RETRIES,
    FIELD_PENDING_EVENT_TIMEOUT_SECONDS,
    FIELD_TIMEOUT_SECONDS,
    FIELD_TOOL_EXECUTION_TIMEOUT_SECONDS,
    FIELD_TOTAL_EXECUTION_TIMEOUT_SECONDS,
    SECTION_EXECUTION,
    SECTION_LOGGING,
    SECTION_SECURITY,
    SECTION_SERVER,
)

# MARK: - Types

RawConfigValue: TypeAlias = JsonValue
RawConfigDict: TypeAlias = dict[str, RawConfigValue]

# MARK: - Environment Variable Names

_ENV_TIMEOUT = "MAIVN_TIMEOUT"
_ENV_MAX_RETRIES = "MAIVN_MAX_RETRIES"
_ENV_EXECUTION_TIMEOUT = "MAIVN_EXECUTION_TIMEOUT"
_ENV_PENDING_EVENT_TIMEOUT = "MAIVN_PENDING_EVENT_TIMEOUT"
_ENV_MAX_PARALLEL_TOOLS = "MAIVN_MAX_PARALLEL_TOOLS"
_ENV_ENABLE_BACKGROUND_EXECUTION = "MAIVN_ENABLE_BACKGROUND_EXECUTION"
_ENV_TOOL_EXECUTION_TIMEOUT = "MAIVN_TOOL_EXECUTION_TIMEOUT"
_ENV_DEPENDENCY_WAIT_TIMEOUT = "MAIVN_DEPENDENCY_WAIT_TIMEOUT"
_ENV_TOTAL_EXECUTION_TIMEOUT = "MAIVN_TOTAL_EXECUTION_TIMEOUT"
_ENV_API_KEY = "MAIVN_API_KEY"
_ENV_DEPLOYMENT_TIMEZONE = "MAIVN_DEPLOYMENT_TIMEZONE"
_ENV_LOG_LEVEL = "MAIVN_LOG_LEVEL"
_ENV_LOG_FORMAT = "MAIVN_LOG_FORMAT"
_ENV_ENABLE_TIMING_LOGS = "MAIVN_ENABLE_TIMING_LOGS"


# MARK: - Configuration Builder


class ConfigurationBuilder:
    """Builder for creating MaivnConfiguration from various sources."""

    @staticmethod
    def from_environment() -> MaivnConfiguration:
        """Create configuration from environment variables.

        This is a convenience method for applications that want to use
        environment-based configuration. The SDK itself does not call this.

        Environment variables:
        - MAIVN_TIMEOUT: Request timeout in seconds
        - MAIVN_MAX_RETRIES: Maximum retry attempts
        - MAIVN_EXECUTION_TIMEOUT: Default execution timeout
        - MAIVN_PENDING_EVENT_TIMEOUT: Timeout for pending events
        - MAIVN_MAX_PARALLEL_TOOLS: Maximum parallel tool executions
        - MAIVN_ENABLE_BACKGROUND_EXECUTION: Enable background execution
        - MAIVN_TOOL_EXECUTION_TIMEOUT: Per-tool execution timeout (seconds)
        - MAIVN_DEPENDENCY_WAIT_TIMEOUT: Dependency resolution timeout (seconds)
        - MAIVN_TOTAL_EXECUTION_TIMEOUT: Total execution timeout (seconds, optional)
        - MAIVN_API_KEY: API key for authentication
        - MAIVN_LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR)
        - MAIVN_LOG_FORMAT: Custom log format string
        - MAIVN_ENABLE_TIMING_LOGS: Enable timing-related logs

        Returns:
            Complete configuration instance
        """
        config_dict = _build_config_dict_from_environment()
        config_dict = remove_none_values(config_dict)
        return MaivnConfiguration.from_dict(cast(ConfigMapping, config_dict))


# MARK: - Private Helpers


def _build_config_dict_from_environment() -> RawConfigDict:
    """Build configuration dictionary from environment variables.

    Returns:
        Configuration dictionary with values from environment variables
    """
    return {
        SECTION_SERVER: _build_server_config(),
        SECTION_EXECUTION: _build_execution_config(),
        SECTION_SECURITY: _build_security_config(),
        SECTION_LOGGING: _build_logging_config(),
    }


def _build_server_config() -> RawConfigDict:
    """Build server configuration from environment variables."""
    return {
        FIELD_TIMEOUT_SECONDS: coerce_float_env(_ENV_TIMEOUT),
        FIELD_MAX_RETRIES: coerce_int_env(_ENV_MAX_RETRIES),
        FIELD_DEPLOYMENT_TIMEZONE: coerce_str_env(_ENV_DEPLOYMENT_TIMEZONE),
    }


def _build_execution_config() -> RawConfigDict:
    """Build execution configuration from environment variables."""
    return {
        FIELD_DEFAULT_TIMEOUT_SECONDS: coerce_float_env(_ENV_EXECUTION_TIMEOUT),
        FIELD_PENDING_EVENT_TIMEOUT_SECONDS: coerce_float_env(_ENV_PENDING_EVENT_TIMEOUT),
        FIELD_MAX_PARALLEL_TOOLS: coerce_int_env(_ENV_MAX_PARALLEL_TOOLS),
        FIELD_ENABLE_BACKGROUND_EXECUTION: coerce_bool_env(_ENV_ENABLE_BACKGROUND_EXECUTION),
        FIELD_TOOL_EXECUTION_TIMEOUT_SECONDS: coerce_float_env(_ENV_TOOL_EXECUTION_TIMEOUT),
        FIELD_DEPENDENCY_WAIT_TIMEOUT_SECONDS: coerce_float_env(_ENV_DEPENDENCY_WAIT_TIMEOUT),
        FIELD_TOTAL_EXECUTION_TIMEOUT_SECONDS: coerce_float_env(_ENV_TOTAL_EXECUTION_TIMEOUT),
    }


def _build_security_config() -> RawConfigDict:
    """Build security configuration from environment variables."""
    return {
        FIELD_API_KEY: coerce_str_env(_ENV_API_KEY),
    }


def _build_logging_config() -> RawConfigDict:
    """Build logging configuration from environment variables."""
    return {
        FIELD_LOG_LEVEL: coerce_str_env(_ENV_LOG_LEVEL),
        FIELD_FORMAT_STRING: coerce_str_env(_ENV_LOG_FORMAT),
        FIELD_ENABLE_TIMING_LOGS: coerce_bool_env(_ENV_ENABLE_TIMING_LOGS),
    }


__all__ = [
    "ConfigurationBuilder",
]
