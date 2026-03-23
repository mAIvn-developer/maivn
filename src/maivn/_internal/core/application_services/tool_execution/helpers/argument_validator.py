"""Argument validation for tool execution.

This module provides validation of tool arguments against function signatures.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from maivn_shared.infrastructure.logging import LoggerProtocol


class ArgumentValidator:
    """Validates tool arguments against function signatures."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        """Initialize argument validator.

        Args:
            logger: Logger for operation tracking
        """
        self._logger: LoggerProtocol | None = logger

    def validate(self, func: Callable[..., Any], args: dict[str, Any]) -> None:
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
            raise ValueError(f"Invalid arguments for function {func.__name__}: {e}") from e


__all__ = ["ArgumentValidator"]
