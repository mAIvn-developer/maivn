"""Configuration builder for convenient configuration creation.

This module provides convenience methods for creating configuration from
environment variables. The SDK itself remains environment-agnostic, but
this builder helps consuming applications easily load config from env vars.
"""

from __future__ import annotations

from typing import Any

from maivn_shared.utils.env import remove_none_values

from maivn._internal.utils.env_parsing import (
    coerce_bool_env,
    coerce_float_env,
    coerce_int_env,
    get_env,
)

from .environment_config import MaivnConfiguration

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
_ENV_MAX_PROMPT_LENGTH = "MAIVN_MAX_PROMPT_LENGTH"
_ENV_TOOL_NAME_HASH_MODULO = "MAIVN_TOOL_NAME_HASH_MODULO"
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
        - MAIVN_MAX_PROMPT_LENGTH: Maximum prompt length for tool naming
        - MAIVN_TOOL_NAME_HASH_MODULO: Modulo for tool name hash generation
        - MAIVN_API_KEY: API key for authentication
        - MAIVN_LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR)
        - MAIVN_LOG_FORMAT: Custom log format string
        - MAIVN_ENABLE_TIMING_LOGS: Enable timing-related logs

        Note: Base URLs are fixed constants and cannot be configured.

        Returns:
            Complete configuration instance
        """
        config_dict = _build_config_dict_from_environment()
        config_dict = remove_none_values(config_dict)
        return MaivnConfiguration.from_dict(config_dict)


# MARK: - Private Helpers


def _build_config_dict_from_environment() -> dict[str, Any]:
    """Build configuration dictionary from environment variables.

    Returns:
        Configuration dictionary with values from environment variables
    """
    return {
        "server": _build_server_config(),
        "execution": _build_execution_config(),
        "security": _build_security_config(),
        "logging": _build_logging_config(),
    }


def _build_server_config() -> dict[str, Any]:
    """Build server configuration from environment variables."""
    return {
        "timeout_seconds": coerce_float_env(_ENV_TIMEOUT),
        "max_retries": coerce_int_env(_ENV_MAX_RETRIES),
        "deployment_timezone": get_env(_ENV_DEPLOYMENT_TIMEZONE),
    }


def _build_execution_config() -> dict[str, Any]:
    """Build execution configuration from environment variables."""
    return {
        "default_timeout_seconds": coerce_float_env(_ENV_EXECUTION_TIMEOUT),
        "pending_event_timeout_seconds": coerce_float_env(_ENV_PENDING_EVENT_TIMEOUT),
        "max_parallel_tools": coerce_int_env(_ENV_MAX_PARALLEL_TOOLS),
        "enable_background_execution": coerce_bool_env(_ENV_ENABLE_BACKGROUND_EXECUTION),
        "tool_execution_timeout_seconds": coerce_float_env(_ENV_TOOL_EXECUTION_TIMEOUT),
        "dependency_wait_timeout_seconds": coerce_float_env(_ENV_DEPENDENCY_WAIT_TIMEOUT),
        "total_execution_timeout_seconds": coerce_float_env(_ENV_TOTAL_EXECUTION_TIMEOUT),
        "max_prompt_length_for_tool_name": coerce_int_env(_ENV_MAX_PROMPT_LENGTH),
        "tool_name_hash_modulo": coerce_int_env(_ENV_TOOL_NAME_HASH_MODULO),
    }


def _build_security_config() -> dict[str, Any]:
    """Build security configuration from environment variables."""
    return {
        "api_key": get_env(_ENV_API_KEY),
    }


def _build_logging_config() -> dict[str, Any]:
    """Build logging configuration from environment variables."""
    return {
        "level": get_env(_ENV_LOG_LEVEL),
        "format_string": get_env(_ENV_LOG_FORMAT),
        "enable_timing_logs": coerce_bool_env(_ENV_ENABLE_TIMING_LOGS),
    }


__all__ = [
    "ConfigurationBuilder",
]
