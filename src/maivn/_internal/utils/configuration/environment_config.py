"""Configuration management for the maivn SDK.

This module provides structured configuration management with type safety and validation.
Configuration values must be provided by the consuming application - the SDK remains
environment-agnostic and does not read environment variables directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# MARK: Constants

DEFAULT_SERVER_BASE_URL = "http://127.0.0.1:8000"
"""Default server URL (will be updated to production URL in the future)."""

VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
"""Valid logging level values."""


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
        self._validate_url(self.base_url, "base_url")
        self._validate_url(self.mock_base_url, "mock_base_url")
        self._validate_positive(self.timeout_seconds, "timeout_seconds")
        self._validate_non_negative(self.max_retries, "max_retries")
        if not isinstance(self.deployment_timezone, str) or not self.deployment_timezone.strip():
            raise ValueError("deployment_timezone must be a non-empty string")

    @staticmethod
    def _validate_url(url: str, field_name: str) -> None:
        """Validate URL format."""
        if not url:
            raise ValueError(f"{field_name} cannot be empty")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{field_name} must start with http:// or https://, got: {url}")

    @staticmethod
    def _validate_positive(value: float, field_name: str) -> None:
        """Validate that a value is positive."""
        if value <= 0:
            raise ValueError(f"{field_name} must be positive, got: {value}")

    @staticmethod
    def _validate_non_negative(value: int, field_name: str) -> None:
        """Validate that a value is non-negative."""
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative, got: {value}")

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> ServerConfiguration:
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
            base_url=config.get("base_url", cls.base_url),
            mock_base_url=config.get("mock_base_url", cls.mock_base_url),
            timeout_seconds=float(config.get("timeout_seconds", cls.timeout_seconds)),
            max_retries=int(config.get("max_retries", cls.max_retries)),
            deployment_timezone=str(
                config.get("deployment_timezone", getattr(cls, "deployment_timezone", "UTC"))
            ),
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

    max_prompt_length_for_tool_name: int = 30
    tool_name_hash_modulo: int = 10000

    def __post_init__(self) -> None:
        """Validate configuration values.

        Raises:
            ValueError: If any configuration value is invalid
        """
        self._validate_positive(self.default_timeout_seconds, "default_timeout_seconds")
        self._validate_non_negative(
            self.pending_event_timeout_seconds, "pending_event_timeout_seconds"
        )
        self._validate_positive(self.max_parallel_tools, "max_parallel_tools")
        self._validate_positive(
            self.tool_execution_timeout_seconds, "tool_execution_timeout_seconds"
        )
        self._validate_positive(
            self.dependency_wait_timeout_seconds, "dependency_wait_timeout_seconds"
        )
        self._validate_optional_positive(
            self.total_execution_timeout_seconds, "total_execution_timeout_seconds"
        )

    @staticmethod
    def _validate_positive(value: float | int, field_name: str) -> None:
        """Validate that a value is positive."""
        if value <= 0:
            raise ValueError(f"{field_name} must be positive, got: {value}")

    @staticmethod
    def _validate_non_negative(value: float, field_name: str) -> None:
        """Validate that a value is non-negative."""
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative, got: {value}")

    @staticmethod
    def _validate_optional_positive(value: float | None, field_name: str) -> None:
        """Validate that an optional value is positive if provided."""
        if value is not None and value <= 0:
            raise ValueError(f"{field_name} must be positive (or None for no limit), got: {value}")

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> ExecutionConfiguration:
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
                - max_prompt_length_for_tool_name: Maximum prompt length for tool naming
                - tool_name_hash_modulo: Modulo for tool name hash generation

        Returns:
            Execution configuration instance
        """
        enable_bg = _parse_bool(
            config.get("enable_background_execution", cls.enable_background_execution)
        )

        total_timeout = config.get(
            "total_execution_timeout_seconds", cls.total_execution_timeout_seconds
        )
        if total_timeout is not None:
            total_timeout = float(total_timeout)

        return cls(
            default_timeout_seconds=float(
                config.get("default_timeout_seconds", cls.default_timeout_seconds)
            ),
            pending_event_timeout_seconds=float(
                config.get("pending_event_timeout_seconds", cls.pending_event_timeout_seconds)
            ),
            max_parallel_tools=int(config.get("max_parallel_tools", cls.max_parallel_tools)),
            enable_background_execution=enable_bg,
            tool_execution_timeout_seconds=float(
                config.get("tool_execution_timeout_seconds", cls.tool_execution_timeout_seconds)
            ),
            dependency_wait_timeout_seconds=float(
                config.get("dependency_wait_timeout_seconds", cls.dependency_wait_timeout_seconds)
            ),
            total_execution_timeout_seconds=total_timeout,
            max_prompt_length_for_tool_name=int(
                config.get("max_prompt_length_for_tool_name", cls.max_prompt_length_for_tool_name)
            ),
            tool_name_hash_modulo=int(
                config.get("tool_name_hash_modulo", cls.tool_name_hash_modulo)
            ),
        )


# MARK: - Security Configuration


@dataclass(frozen=True)
class SecurityConfiguration:
    """Configuration for security and authentication."""

    api_key: str | None = None
    require_api_key: bool = True

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> SecurityConfiguration:
        """Create configuration from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - api_key: API key for authentication
                - require_api_key: Whether API key is required

        Returns:
            Security configuration instance
        """
        require_key = _parse_bool(config.get("require_api_key", cls.require_api_key))

        return cls(
            api_key=config.get("api_key"),
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
    def from_dict(cls, config: dict[str, Any]) -> LoggingConfiguration:
        """Create configuration from dictionary.

        Args:
            config: Configuration dictionary with keys:
                - level: Logging level (DEBUG, INFO, WARNING, ERROR)
                - format_string: Custom log format string
                - enable_timing_logs: Enable timing-related logs

        Returns:
            Logging configuration instance
        """
        level = config.get("level", cls.level)
        if isinstance(level, str):
            level = level.upper()

        enable_timing = _parse_bool(config.get("enable_timing_logs", cls.enable_timing_logs))

        return cls(
            level=str(level),
            format_string=config.get("format_string", cls.format_string),
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
    def from_dict(cls, config: dict[str, Any]) -> MaivnConfiguration:
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
            server=ServerConfiguration.from_dict(config.get("server", {})),
            execution=ExecutionConfiguration.from_dict(config.get("execution", {})),
            security=SecurityConfiguration.from_dict(config.get("security", {})),
            logging=LoggingConfiguration.from_dict(config.get("logging", {})),
        )

    def validate(self) -> list[str]:
        """Validate configuration and return any validation errors.

        Returns:
            List of validation error messages, empty if valid
        """
        errors: list[str] = []

        if not self.server.base_url.startswith(("http://", "https://")):
            errors.append("Server base_url must start with http:// or https://")

        if self.server.timeout_seconds <= 0:
            errors.append("Server timeout must be positive")

        if self.execution.default_timeout_seconds <= 0:
            errors.append("Execution timeout must be positive")

        if self.execution.max_parallel_tools <= 0:
            errors.append("Max parallel tools must be positive")

        if self.security.require_api_key and not self.security.api_key:
            errors.append("API key is required but not provided")

        if self.logging.level not in VALID_LOG_LEVELS:
            errors.append(f"Log level must be one of: {', '.join(sorted(VALID_LOG_LEVELS))}")

        return errors


# MARK: Utility Functions


def _parse_bool(value: Any) -> bool:
    """Parse a value as boolean, handling string representations.

    Args:
        value: Value to parse (bool, str, or other)

    Returns:
        Boolean interpretation of the value
    """
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


# MARK: Exports

__all__ = [
    "DEFAULT_SERVER_BASE_URL",
    "ExecutionConfiguration",
    "LoggingConfiguration",
    "MaivnConfiguration",
    "SecurityConfiguration",
    "ServerConfiguration",
]
