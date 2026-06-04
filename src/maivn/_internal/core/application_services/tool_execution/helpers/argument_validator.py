"""Argument validation for tool execution.

This module provides validation of tool arguments against function signatures.
"""

# pyright: strict
from __future__ import annotations

import inspect
from collections.abc import Callable

from maivn_shared.infrastructure.logging import LoggerProtocol

# MARK: Argument Validator


class ArgumentValidator:
    """Validates tool arguments against function signatures."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        """Initialize argument validator.

        Args:
            logger: Logger for operation tracking
        """
        self._logger: LoggerProtocol | None = logger

    def validate(self, func: Callable[..., object], args: dict[str, object]) -> None:
        """Validate that provided arguments match function signature.

        Args:
            func: Function to validate against
            args: Arguments to validate

        Raises:
            ValueError: If arguments don't match signature
        """
        try:
            sig = inspect.signature(func)
            bound_args = sig.bind(**args)
            bound_args.apply_defaults()
        except TypeError as e:
            raise ValueError(f"Invalid arguments for function {_callable_name(func)}: {e}") from e


# MARK: Helpers


def _callable_name(func: object) -> str:
    name = getattr(func, "__name__", "<function>")
    return name if isinstance(name, str) and name else "<function>"


__all__ = ["ArgumentValidator"]
