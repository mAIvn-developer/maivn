"""Function tool execution with validation and deserialization.

This module handles execution of FunctionTool instances with:
- Argument validation against function signature
- Pydantic model deserialization
- Enhanced error handling
"""

# pyright: strict
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import cast

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import FunctionTool
from maivn._internal.core.exceptions import ArgumentValidationError, ToolExecutionError

from ...helpers import PydanticDeserializer

# MARK: Function Executor


class FunctionExecutor:
    """Executes function tools with validation and deserialization."""

    def __init__(
        self,
        *,
        logger: LoggerProtocol | None = None,
        deserializer: PydanticDeserializer | None = None,
    ) -> None:
        """Initialize function executor.

        Args:
            logger: Logger for operation tracking
            deserializer: Pydantic deserializer for argument conversion
        """
        self._logger: LoggerProtocol | None = logger
        self._deserializer: PydanticDeserializer = deserializer or PydanticDeserializer(
            logger=logger
        )

    def execute(self, tool: FunctionTool, args: dict[str, object]) -> object:
        """Execute a function tool with enhanced error handling.

        Args:
            tool: Function tool to execute
            args: Arguments for execution

        Returns:
            Function execution result

        Raises:
            TypeError: If tool has no callable function
            ValueError: If arguments don't match signature
        """
        func = cast(object, getattr(tool, "func", None))
        if not callable(func):
            raise ToolExecutionError(
                tool_id=_tool_name(tool),
                reason="FunctionTool has no callable 'func'",
            )
        callable_func = func

        if self._logger:
            self._logger.info(
                "[TOOL_EXEC] _execute_function_tool called for %s",
                _callable_name(callable_func),
            )

        try:
            # Validate arguments match function signature
            if self._logger:
                self._logger.info("[TOOL_EXEC] Validating args...")
            self._validate_args(callable_func, args)

            # Deserialize dict arguments to Pydantic models based on type hints
            if self._logger:
                self._logger.info("[TOOL_EXEC] Deserializing Pydantic args...")
            deserialized_args = self._deserializer.deserialize_args(callable_func, args)

            if self._logger:
                self._logger.info("[TOOL_EXEC] Deserialization complete, calling function...")

            # Execute the function with properly typed arguments
            return callable_func(**deserialized_args)

        except TypeError as e:
            if "unexpected keyword argument" in str(e) or "missing" in str(e):
                # Provide better error message for argument mismatches
                sig = inspect.signature(callable_func)
                expected_params = list(sig.parameters.keys())
                provided_params = list(args.keys())

                raise ArgumentValidationError(
                    tool_name=_callable_name(callable_func),
                    expected_params=expected_params,
                    provided_params=provided_params,
                ) from e
            raise

    def _validate_args(self, func: Callable[..., object], args: dict[str, object]) -> None:
        """Validate that provided arguments match function signature.

        Args:
            func: Function to validate against
            args: Arguments to validate

        Raises:
            ArgumentValidationError: If arguments don't match signature
        """
        try:
            sig = inspect.signature(func)
            bound_args = sig.bind(**args)
            bound_args.apply_defaults()
        except TypeError as e:
            raise ArgumentValidationError(
                tool_name=_callable_name(func),
                details=str(e),
            ) from e


# MARK: Helpers


def _callable_name(func: object) -> str:
    name = cast(object, getattr(func, "__name__", "<function>"))
    return name if isinstance(name, str) and name else "<function>"


def _tool_name(tool: object) -> str:
    name = cast(object, getattr(tool, "name", "unknown"))
    return name if isinstance(name, str) and name else "unknown"


__all__ = ["FunctionExecutor"]
