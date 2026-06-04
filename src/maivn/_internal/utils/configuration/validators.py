"""Shared configuration validators."""

# pyright: strict
from __future__ import annotations

from typing import Final

# MARK: Constants

HTTP_URL_PREFIXES: Final = ("http://", "https://")
VALID_LOG_LEVELS: Final = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


# MARK: Field Validators


def validate_url(url: str, field_name: str) -> None:
    """Validate URL format."""
    if not url:
        raise ValueError(f"{field_name} cannot be empty")
    if not url.startswith(HTTP_URL_PREFIXES):
        raise ValueError(f"{field_name} must start with http:// or https://, got: {url}")


def validate_positive(value: float | int, field_name: str) -> None:
    """Validate that a value is positive."""
    if value <= 0:
        raise ValueError(f"{field_name} must be positive, got: {value}")


def validate_non_negative(value: float | int, field_name: str) -> None:
    """Validate that a value is non-negative."""
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got: {value}")


def validate_optional_positive(value: float | None, field_name: str) -> None:
    """Validate that an optional value is positive when provided."""
    if value is not None and value <= 0:
        raise ValueError(f"{field_name} must be positive (or None for no limit), got: {value}")


def validate_non_empty_string(value: object, field_name: str) -> None:
    """Validate that a value is a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


# MARK: Aggregate Validators


def required_api_key_error(*, require_api_key: bool, api_key: str | None) -> str | None:
    """Return the security validation error, if any."""
    if require_api_key and not api_key:
        return "API key is required but not provided"
    return None


def log_level_error(level: str) -> str | None:
    """Return the log-level validation error, if any."""
    if level not in VALID_LOG_LEVELS:
        return f"Log level must be one of: {', '.join(sorted(VALID_LOG_LEVELS))}"
    return None
