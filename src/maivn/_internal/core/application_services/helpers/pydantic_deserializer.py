"""Pydantic model deserialization for tool arguments.
Converts dict arguments into Pydantic model instances based on function type hints.
Supports union types and lists of models, with cached type hint resolution.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin

from maivn_shared.infrastructure.logging import LoggerProtocol

# MARK: - PydanticDeserializer


class PydanticDeserializer:
    """Deserializes dict arguments to Pydantic models based on type hints.

    Handles:
    - Direct Pydantic models: MyModel
    - Union types: MyModel | dict
    - List of models: list[MyModel]
    - Python 3.10+ union syntax: X | Y

    Performance: Caches type hints by function ID for 10-100x speedup on repeated calls.
    """

    def __init__(self, logger: LoggerProtocol | None = None) -> None:
        """Initialize deserializer with optional logger.

        Args:
            logger: Optional logger for debug output
        """
        self._logger: LoggerProtocol | None = logger
        self._type_hints_cache: dict[int, dict[str, type]] = {}

    # MARK: - Public API

    def deserialize_args(self, func: Callable, args: dict[str, Any]) -> dict[str, Any]:
        """Main entry point for deserialization.

        Args:
            func: Function to inspect for type hints
            args: Raw arguments (may contain dicts)

        Returns:
            Arguments with dicts converted to Pydantic models where appropriate
        """
        if not self._is_pydantic_available():
            return args

        type_hints = self._get_type_hints(func)
        if not type_hints:
            return args

        return self._deserialize_all_args_safe(func, args, type_hints)

    # MARK: - Type Hint Resolution

    def _get_type_hints(self, func: Callable) -> dict[str, type]:
        """Get type hints for a function, with fallback to signature.

        Uses caching for 10-100x performance improvement on repeated calls.

        Args:
            func: Function to get type hints from

        Returns:
            Dict mapping parameter names to their types
        """
        func_id = id(func)
        if func_id in self._type_hints_cache:
            return self._type_hints_cache[func_id]

        hints = self._resolve_type_hints(func)
        self._type_hints_cache[func_id] = hints
        return hints

    def _resolve_type_hints(self, func: Callable) -> dict[str, type]:
        """Resolve type hints from function, with signature fallback.

        Args:
            func: Function to get type hints from

        Returns:
            Dict mapping parameter names to their types
        """
        try:
            from typing import get_type_hints

            return get_type_hints(func)
        except Exception as e:
            if self._logger:
                self._logger.debug(
                    "get_type_hints() failed for %s: %s. Using signature.",
                    func.__name__,
                    str(e),
                )
            return self._get_hints_from_signature(func)

    def _get_hints_from_signature(self, func: Callable) -> dict[str, type]:
        """Extract type hints from function signature.

        Args:
            func: Function to inspect

        Returns:
            Dict mapping parameter names to their types
        """
        sig = inspect.signature(func)
        return {
            name: param.annotation
            for name, param in sig.parameters.items()
            if param.annotation != inspect.Parameter.empty
        }

    # MARK: - Deserialization Logic

    def _deserialize_all_args_safe(
        self,
        func: Callable,
        args: dict[str, Any],
        type_hints: dict[str, type],
    ) -> dict[str, Any]:
        """Safely deserialize all arguments with error handling.

        Args:
            func: Function being called
            args: Raw arguments
            type_hints: Type hints for the function

        Returns:
            Deserialized arguments or original args on failure
        """
        try:
            return self._deserialize_all_args(args, type_hints)
        except Exception as e:
            if self._logger:
                self._logger.warning(
                    "Pydantic deserialization failed for %s: %s. Using raw args.",
                    func.__name__,
                    str(e),
                )
            return args

    def _deserialize_all_args(
        self,
        args: dict[str, Any],
        type_hints: dict[str, type],
    ) -> dict[str, Any]:
        """Deserialize all arguments based on type hints.

        Args:
            args: Raw arguments
            type_hints: Type hints for the function

        Returns:
            Deserialized arguments
        """
        return {
            param_name: self._deserialize_param(param_name, param_value, type_hints)
            for param_name, param_value in args.items()
        }

    def _deserialize_param(
        self,
        param_name: str,
        param_value: Any,
        type_hints: dict[str, type],
    ) -> Any:
        """Deserialize a single parameter.

        Args:
            param_name: Parameter name
            param_value: Parameter value
            type_hints: Type hints for the function

        Returns:
            Deserialized value or original value
        """
        annotation = type_hints.get(param_name)
        if annotation is None:
            return param_value
        return self._deserialize_value(param_value, annotation, param_name)

    def _deserialize_value(
        self,
        value: Any,
        annotation: type,
        param_name: str,
    ) -> Any:
        """Deserialize a single value based on its type annotation.

        Args:
            value: Value to deserialize
            annotation: Type annotation
            param_name: Parameter name (for logging)

        Returns:
            Deserialized value
        """
        if self._is_union_type(annotation):
            return self._handle_union(value, annotation, param_name)

        result = self._try_deserialize(value, annotation, param_name)
        return result if result is not None else value

    # MARK: - Type Checking

    def _is_pydantic_available(self) -> bool:
        """Check if Pydantic is available.

        Returns:
            True if Pydantic can be imported
        """
        try:
            from pydantic import BaseModel  # noqa: F401

            return True
        except ImportError:
            if self._logger:
                self._logger.debug("Pydantic not available, skipping deserialization")
            return False

    def _is_union_type(self, annotation: type) -> bool:
        """Check if annotation is a Union type.

        Handles both typing.Union and Python 3.10+ X | Y syntax.

        Args:
            annotation: Type annotation to check

        Returns:
            True if annotation is a Union type
        """
        if get_origin(annotation) is Union:
            return True

        try:
            from types import UnionType  # type: ignore

            return isinstance(annotation, UnionType)
        except ImportError:
            return False

    def _is_list_type(self, target_type: type) -> bool:
        """Check if target type is a list type.

        Args:
            target_type: Type to check

        Returns:
            True if target is list[T]
        """
        return get_origin(target_type) is list

    def _is_pydantic_model(self, target_type: type) -> bool:
        """Check if target type is a Pydantic BaseModel subclass.

        Args:
            target_type: Type to check

        Returns:
            True if target is a Pydantic model
        """
        try:
            from pydantic import BaseModel

            return isinstance(target_type, type) and issubclass(target_type, BaseModel)
        except (TypeError, AttributeError, ImportError):
            return False

    # MARK: - Union Handling

    def _handle_union(
        self,
        value: Any,
        annotation: type,
        param_name: str,
    ) -> Any:
        """Handle Union type deserialization.

        Tries each type in the Union until one succeeds.

        Args:
            value: Value to deserialize
            annotation: Union type annotation
            param_name: Parameter name (for logging)

        Returns:
            Deserialized value or original value if all types fail
        """
        for type_arg in get_args(annotation):
            result = self._try_deserialize(value, type_arg, param_name)
            if result is not None:
                return result
        return value

    # MARK: - Type-Specific Deserialization

    def _try_deserialize(
        self,
        value: Any,
        target_type: type,
        param_name: str,
    ) -> Any | None:
        """Try to deserialize value to target type.

        Args:
            value: Value to deserialize
            target_type: Target type
            param_name: Parameter name (for logging)

        Returns:
            Deserialized value, or None if deserialization failed/not applicable
        """
        if self._is_list_type(target_type):
            return self._deserialize_list(value, target_type)

        if self._is_pydantic_model(target_type):
            return self._deserialize_pydantic_model(value, target_type, param_name)

        return None

    def _deserialize_list(
        self,
        value: Any,
        target_type: type,
    ) -> list[Any] | None:
        """Deserialize list[Model] type.

        Args:
            value: Value to deserialize
            target_type: list[T] type

        Returns:
            Deserialized list or None if not applicable
        """
        if not isinstance(value, list):
            return None

        type_args = get_args(target_type)
        if not type_args:
            return None

        element_type = type_args[0]
        if not self._is_pydantic_model(element_type):
            return None

        try:
            return [element_type(**item) if isinstance(item, dict) else item for item in value]
        except Exception as e:
            if self._logger:
                self._logger.debug("[DESERIALIZER] Failed to deserialize list elements: %s", e)
            return None

    def _deserialize_pydantic_model(
        self,
        value: Any,
        target_type: type,
        param_name: str,
    ) -> Any | None:
        """Deserialize dict to Pydantic model.

        Args:
            value: Value to deserialize
            target_type: Pydantic model class
            param_name: Parameter name (for logging)

        Returns:
            Pydantic model instance or None if not applicable
        """
        if isinstance(value, target_type):
            return value

        if not isinstance(value, dict):
            return None

        try:
            return target_type(**value)
        except Exception as e:
            if self._logger:
                self._logger.debug(
                    "Could not convert %s to %s: %s",
                    param_name,
                    target_type.__name__,
                    str(e),
                )
            return None


__all__ = ["PydanticDeserializer"]
