"""Configuration management for the maivn SDK.

This module provides structured configuration management with type safety and validation.
It loads ``.env`` at import time to preserve the SDK's historical environment behavior
before computing the default server base URL from ``MAIVN_SERVER_BASE_URL``.
"""

# pyright: strict
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import SupportsFloat, SupportsIndex, SupportsInt, TypeAlias, cast

from dotenv import load_dotenv
from pydantic import JsonValue

from ..env_parsing import coerce_bool_value
from .keys import (
    FIELD_API_KEY,
    FIELD_BASE_URL,
    FIELD_DEFAULT_TIMEOUT_SECONDS,
    FIELD_DEPENDENCY_WAIT_TIMEOUT_SECONDS,
    FIELD_DEPLOYMENT_TIMEZONE,
    FIELD_ENABLE_BACKGROUND_EXECUTION,
    FIELD_ENABLE_TIMING_LOGS,
    FIELD_FORMAT_STRING,
    FIELD_LOG_LEVEL,
    FIELD_MAX_PARALLEL_TOOLS,
    FIELD_MAX_RETRIES,
    FIELD_MOCK_BASE_URL,
    FIELD_PENDING_EVENT_TIMEOUT_SECONDS,
    FIELD_REQUIRE_API_KEY,
    FIELD_TIMEOUT_SECONDS,
    FIELD_TOOL_EXECUTION_TIMEOUT_SECONDS,
    FIELD_TOTAL_EXECUTION_TIMEOUT_SECONDS,
    SECTION_EXECUTION,
    SECTION_LOGGING,
    SECTION_SECURITY,
    SECTION_SERVER,
)
from .validators import (
    log_level_error,
    required_api_key_error,
    validate_non_empty_string,
    validate_non_negative,
    validate_optional_positive,
    validate_positive,
    validate_url,
)

# MARK: Constants

_ = load_dotenv()

ConfigMapping: TypeAlias = Mapping[str, JsonValue]
FloatInput: TypeAlias = str | bytes | SupportsFloat | SupportsIndex
IntInput: TypeAlias = str | bytes | SupportsInt | SupportsIndex
_EMPTY_CONFIG: ConfigMapping = MappingProxyType({})

# Default server URL, overridable via MAIVN_SERVER_BASE_URL env var.
DEFAULT_SERVER_BASE_URL = os.environ.get("MAIVN_SERVER_BASE_URL", "https://api.maivn.io")


# MARK: Configuration Classes

# MARK: - Server Configuration


@dataclass(frozen=True)
class ServerConfiguration:
    """Configuration for server connections with runtime validation.

    Raises:
        ValueError: If configuration values are invalid
    """

    base_url: str = DEFAULT_SERVER_BASE_URL
    mock_base_url: str = DEFAULT_SERVER_BASE_URL
    timeout_seconds: float = 600.0
    max_retries: int = 3
    # NOTE: deployment_timezone must be an IANA time zone identifier (e.g. "America/New_York").
    deployment_timezone: str = "UTC"

    def __post_init__(self) -> None:
        """Validate configuration values.

        Raises:
            ValueError: If any configuration value is invalid
        """
        validate_url(self.base_url, FIELD_BASE_URL)
        validate_url(self.mock_base_url, FIELD_MOCK_BASE_URL)
        validate_positive(self.timeout_seconds, FIELD_TIMEOUT_SECONDS)
        validate_non_negative(self.max_retries, FIELD_MAX_RETRIES)
        validate_non_empty_string(self.deployment_timezone, FIELD_DEPLOYMENT_TIMEZONE)

    @classmethod
    def from_dict(cls, config: ConfigMapping) -> ServerConfiguration:
        """Create configuration from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - base_url: Server base URL
                - mock_base_url: Mock server base URL
                - timeout_seconds: Request timeout in seconds
                - max_retries: Maximum retry attempts

        Returns:
            Server configuration instance
        """
        return cls(
            base_url=cast(str, config.get(FIELD_BASE_URL, cls.base_url)),
            mock_base_url=cast(str, config.get(FIELD_MOCK_BASE_URL, cls.mock_base_url)),
            timeout_seconds=_to_float(config.get(FIELD_TIMEOUT_SECONDS, cls.timeout_seconds)),
            max_retries=_to_int(config.get(FIELD_MAX_RETRIES, cls.max_retries)),
            deployment_timezone=str(config.get(FIELD_DEPLOYMENT_TIMEZONE, cls.deployment_timezone)),
        )


# MARK: - Execution Configuration


@dataclass(frozen=True)
class ExecutionConfiguration:
    """Configuration for tool and agent execution with runtime validation.

    Raises:
        ValueError: If configuration values are invalid
    """

    default_timeout_seconds: float = 600.0
    pending_event_timeout_seconds: float = 0.2
    max_parallel_tools: int = 8
    enable_background_execution: bool = True

    tool_execution_timeout_seconds: float = 900.0
    """Per-tool execution timeout in seconds.

    Controls how long to wait for EACH individual operation (function tool,
    LLM call, agent invocation) to complete. If exceeded, that operation fails
    but others continue. Default: 900 seconds (15 minutes).
    """

    dependency_wait_timeout_seconds: float = 300.0
    """Dependency resolution timeout in seconds.

    When a tool depends on another tool's output (via ref_id), this controls
    how long to wait for the dependency result. Rarely hit in normal reactive
    execution since dependencies complete before dependents start.
    Default: 300 seconds (5 minutes).
    """

    total_execution_timeout_seconds: float | None = 7200.0
    """Total execution timeout in seconds.

    Upper bound on the entire execution session. The session times out after
    this duration regardless of individual tool progress. Prevents runaway
    sessions from consuming resources indefinitely.

    Set to None explicitly if you need unlimited execution time for very
    long-running workflows. Default: 7200 seconds (2 hours).
    """

    def __post_init__(self) -> None:
        """Validate configuration values.

        Raises:
            ValueError: If any configuration value is invalid
        """
        validate_positive(self.default_timeout_seconds, FIELD_DEFAULT_TIMEOUT_SECONDS)
        validate_non_negative(
            self.pending_event_timeout_seconds, FIELD_PENDING_EVENT_TIMEOUT_SECONDS
        )
        validate_positive(self.max_parallel_tools, FIELD_MAX_PARALLEL_TOOLS)
        validate_positive(self.tool_execution_timeout_seconds, FIELD_TOOL_EXECUTION_TIMEOUT_SECONDS)
        validate_positive(
            self.dependency_wait_timeout_seconds, FIELD_DEPENDENCY_WAIT_TIMEOUT_SECONDS
        )
        validate_optional_positive(
            self.total_execution_timeout_seconds, FIELD_TOTAL_EXECUTION_TIMEOUT_SECONDS
        )

    @classmethod
    def from_dict(cls, config: ConfigMapping) -> ExecutionConfiguration:
        """Create configuration from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - default_timeout_seconds: Default execution timeout
                - pending_event_timeout_seconds: Timeout for pending events
                - max_parallel_tools: Maximum parallel tool executions
                - enable_background_execution: Enable background execution
                - tool_execution_timeout_seconds: Per-tool execution timeout
                - dependency_wait_timeout_seconds: Dependency resolution timeout
                - total_execution_timeout_seconds: Total execution timeout (None = no limit)

        Returns:
            Execution configuration instance
        """
        enable_bg = coerce_bool_value(
            config.get(FIELD_ENABLE_BACKGROUND_EXECUTION, cls.enable_background_execution)
        )

        total_timeout = config.get(
            FIELD_TOTAL_EXECUTION_TIMEOUT_SECONDS, cls.total_execution_timeout_seconds
        )
        if total_timeout is not None:
            total_timeout = _to_float(total_timeout)

        return cls(
            default_timeout_seconds=_to_float(
                config.get(FIELD_DEFAULT_TIMEOUT_SECONDS, cls.default_timeout_seconds)
            ),
            pending_event_timeout_seconds=_to_float(
                config.get(FIELD_PENDING_EVENT_TIMEOUT_SECONDS, cls.pending_event_timeout_seconds)
            ),
            max_parallel_tools=_to_int(
                config.get(FIELD_MAX_PARALLEL_TOOLS, cls.max_parallel_tools)
            ),
            enable_background_execution=enable_bg,
            tool_execution_timeout_seconds=_to_float(
                config.get(FIELD_TOOL_EXECUTION_TIMEOUT_SECONDS, cls.tool_execution_timeout_seconds)
            ),
            dependency_wait_timeout_seconds=_to_float(
                config.get(
                    FIELD_DEPENDENCY_WAIT_TIMEOUT_SECONDS,
                    cls.dependency_wait_timeout_seconds,
                )
            ),
            total_execution_timeout_seconds=total_timeout,
        )


# MARK: - Security Configuration


@dataclass(frozen=True)
class SecurityConfiguration:
    """Configuration for security and authentication."""

    api_key: str | None = None
    require_api_key: bool = True

    @classmethod
    def from_dict(cls, config: ConfigMapping) -> SecurityConfiguration:
        """Create configuration from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - api_key: API key for authentication
                - require_api_key: Whether API key is required

        Returns:
            Security configuration instance
        """
        require_key = coerce_bool_value(config.get(FIELD_REQUIRE_API_KEY, cls.require_api_key))

        return cls(
            api_key=cast(str | None, config.get(FIELD_API_KEY)),
            require_api_key=require_key,
        )


# MARK: - Logging Configuration


@dataclass(frozen=True)
class LoggingConfiguration:
    """Configuration for logging behavior."""

    level: str = "INFO"
    format_string: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    enable_timing_logs: bool = True

    @classmethod
    def from_dict(cls, config: ConfigMapping) -> LoggingConfiguration:
        """Create configuration from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - level: Logging level (DEBUG, INFO, WARNING, ERROR)
                - format_string: Custom log format string
                - enable_timing_logs: Enable timing-related logs

        Returns:
            Logging configuration instance
        """
        level = config.get(FIELD_LOG_LEVEL, cls.level)
        if isinstance(level, str):
            level = level.upper()

        enable_timing = coerce_bool_value(
            config.get(FIELD_ENABLE_TIMING_LOGS, cls.enable_timing_logs)
        )

        return cls(
            level=str(level),
            format_string=cast(str, config.get(FIELD_FORMAT_STRING, cls.format_string)),
            enable_timing_logs=enable_timing,
        )


# MARK: - Main Configuration


@dataclass(frozen=True)
class MaivnConfiguration:
    """Main configuration object combining all configuration areas."""

    server: ServerConfiguration = field(default_factory=ServerConfiguration)
    execution: ExecutionConfiguration = field(default_factory=ExecutionConfiguration)
    security: SecurityConfiguration = field(default_factory=SecurityConfiguration)
    logging: LoggingConfiguration = field(default_factory=LoggingConfiguration)

    @classmethod
    def from_dict(cls, config: ConfigMapping) -> MaivnConfiguration:
        """Create complete configuration from dictionary.

        Args:
            config: Configuration dictionary with nested keys for each section:
                - server: ServerConfiguration options
                - execution: ExecutionConfiguration options
                - security: SecurityConfiguration options
                - logging: LoggingConfiguration options

        Returns:
            Complete configuration instance
        """
        return cls(
            server=ServerConfiguration.from_dict(_get_config_section(config, SECTION_SERVER)),
            execution=ExecutionConfiguration.from_dict(
                _get_config_section(config, SECTION_EXECUTION)
            ),
            security=SecurityConfiguration.from_dict(_get_config_section(config, SECTION_SECURITY)),
            logging=LoggingConfiguration.from_dict(_get_config_section(config, SECTION_LOGGING)),
        )

    def validate(self) -> list[str]:
        """Validate configuration and return any validation errors.

        Returns:
            List of validation error messages, empty if valid
        """
        errors: list[str] = []

        security_error = required_api_key_error(
            require_api_key=self.security.require_api_key,
            api_key=self.security.api_key,
        )
        if security_error is not None:
            errors.append(security_error)

        logging_error = log_level_error(self.logging.level)
        if logging_error is not None:
            errors.append(logging_error)

        return errors


# MARK: Utility Functions


def _get_config_section(config: ConfigMapping, key: str) -> ConfigMapping:
    return cast(ConfigMapping, config.get(key, _EMPTY_CONFIG))


def _to_float(value: object) -> float:
    return float(cast(FloatInput, value))


def _to_int(value: object) -> int:
    return int(cast(IntInput, value))


# MARK: Exports

__all__ = [
    "DEFAULT_SERVER_BASE_URL",
    "ExecutionConfiguration",
    "LoggingConfiguration",
    "MaivnConfiguration",
    "SecurityConfiguration",
    "ServerConfiguration",
]
