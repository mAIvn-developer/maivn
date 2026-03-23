"""Domain logger utilities.
Provides a minimal logger protocol and helpers for safe domain-layer logging.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

# MARK: - Protocols


class DomainLoggerProtocol(Protocol):
    """Protocol defining the interface for domain-level logging."""

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def info(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None: ...

    def error(self, message: str, *args: Any, **kwargs: Any) -> None: ...


# MARK: - Null Logger


class _NullLogger:
    """No-op logger implementation for when no logger is provided."""

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        pass

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        pass

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        pass

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        pass


# MARK: - Factory Functions


def ensure_domain_logger(logger: Any | None = None) -> DomainLoggerProtocol:
    """Ensure domain layer logging never depends on infrastructure modules.

    Args:
        logger: Optional logger instance to validate and use.

    Returns:
        A logger conforming to DomainLoggerProtocol, or a null logger if none provided.
    """
    if logger is not None and _is_valid_logger(logger):
        return cast(DomainLoggerProtocol, logger)
    return _NullLogger()


# MARK: - Private Helpers


def _is_valid_logger(logger: Any) -> bool:
    """Check if the provided object has all required logging methods."""
    required_methods = ("debug", "info", "warning", "error")
    return all(hasattr(logger, method) for method in required_methods)


# MARK: - Exports


__all__ = ["DomainLoggerProtocol", "ensure_domain_logger"]
