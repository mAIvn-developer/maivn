"""Pydantic model deserialization for tool arguments.
Converts dict arguments into Pydantic model instances based on function type hints.
Supports union types and lists of models, with cached type hint resolution.
"""

# pyright: strict
from __future__ import annotations

import inspect
import weakref
from collections.abc import Callable
from types import UnionType
from typing import TypeAlias, TypeGuard, cast, get_args, get_origin

from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import BaseModel, TypeAdapter, ValidationError

# MARK: Types

CallableObject: TypeAlias = Callable[..., object]
TypeHints: TypeAlias = dict[str, object]
DeserializedArgs: TypeAlias = dict[str, object]


class _OmitArg:
    """Sentinel for an invalid defaulted argument that should use the Python default."""


_OMIT_ARG = _OmitArg()


# MARK: PydanticDeserializer


class PydanticDeserializer:
    """Deserializes dict arguments to Pydantic models based on type hints.

    Handles:
    - Direct Pydantic models: MyModel
    - Union types: MyModel | dict
    - List of models: list[MyModel]
    - Python 3.10+ union syntax: X | Y

    Performance: Caches type hints by callable identity for repeated calls.
    """

    def __init__(self, logger: LoggerProtocol | None = None) -> None:
        """Initialize deserializer with optional logger.

        Args:
            logger: Optional logger for debug output
        """
        self._logger: LoggerProtocol | None = logger
        self._type_hints_cache: weakref.WeakKeyDictionary[CallableObject, TypeHints] = (
            weakref.WeakKeyDictionary()
        )
        self._strong_type_hints_cache: dict[int, tuple[CallableObject, TypeHints]] = {}
        self._rebuilt_models: weakref.WeakSet[type[BaseModel]] = weakref.WeakSet()

    # MARK: - Public API

    def deserialize_args(self, func: CallableObject, args: dict[str, object]) -> DeserializedArgs:
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

    def _get_type_hints(self, func: CallableObject) -> TypeHints:
        """Get type hints for a function, with fallback to signature.

        Uses caching for 10-100x performance improvement on repeated calls.

        Args:
            func: Function to get type hints from

        Returns:
            Dict mapping parameter names to their types
        """
        cached = self._get_cached_type_hints(func)
        if cached is not None:
            return cached

        hints = self._resolve_type_hints(func)
        self._cache_type_hints(func, hints)
        return hints

    def _get_cached_type_hints(self, func: CallableObject) -> TypeHints | None:
        """Return cached hints without relying on recyclable ``id(func)`` alone."""
        try:
            cached = self._type_hints_cache.get(func)
        except TypeError:
            cached_entry = self._strong_type_hints_cache.get(id(func))
            if cached_entry is None:
                return None
            cached_func, hints = cached_entry
            return hints if cached_func is func else None
        return cached

    def _cache_type_hints(self, func: CallableObject, hints: TypeHints) -> None:
        """Cache hints with a weak key when possible and an identity-checked fallback.

        ``WeakKeyDictionary`` avoids the old ``id(func)`` reuse bug for ordinary
        functions and retained bound methods. Some callable instances are
        unhashable or not weak-referenceable; the fallback stores the callable
        alongside the ``id`` so the key cannot be recycled while the cache entry
        exists and lookup still verifies object identity.
        """
        try:
            self._type_hints_cache[func] = hints
        except TypeError:
            self._strong_type_hints_cache[id(func)] = (func, hints)

    def _resolve_type_hints(self, func: CallableObject) -> TypeHints:
        """Resolve type hints from function, with signature fallback.

        Args:
            func: Function to get type hints from

        Returns:
            Dict mapping parameter names to their types
        """
        try:
            from typing import get_type_hints

            return cast(TypeHints, get_type_hints(func))
        except Exception as exc:  # noqa: BLE001 - fall back when annotations cannot resolve.
            if self._logger:
                self._logger.debug(
                    "get_type_hints() failed for %s: %s. Using signature.",
                    _callable_name(func),
                    str(exc),
                )
            return self._get_hints_from_signature(func)

    def _get_hints_from_signature(self, func: CallableObject) -> TypeHints:
        """Extract type hints from function signature.

        Args:
            func: Function to inspect

        Returns:
            Dict mapping parameter names to their types
        """
        sig = inspect.signature(func)
        hints: TypeHints = {}
        for name, param in sig.parameters.items():
            annotation = cast(object, param.annotation)
            if annotation != inspect.Parameter.empty:
                hints[name] = annotation
        return hints

    # MARK: - Deserialization Logic

    def _deserialize_all_args_safe(
        self,
        func: CallableObject,
        args: dict[str, object],
        type_hints: TypeHints,
    ) -> DeserializedArgs:
        """Safely deserialize all arguments with error handling.

        Args:
            func: Function being called
            args: Raw arguments
            type_hints: Type hints for the function

        Returns:
            Deserialized arguments or original args on failure
        """
        try:
            return self._deserialize_all_args(func, args, type_hints)
        except Exception as exc:  # noqa: BLE001 - preserve raw args on deserializer failure.
            if self._logger:
                self._logger.warning(
                    "Pydantic deserialization failed for %s: %s. Using raw args.",
                    _callable_name(func),
                    str(exc),
                )
            return args

    def _deserialize_all_args(
        self,
        func: CallableObject,
        args: dict[str, object],
        type_hints: TypeHints,
    ) -> DeserializedArgs:
        """Deserialize all arguments based on type hints.

        Args:
            func: Function being called
            args: Raw arguments
            type_hints: Type hints for the function

        Returns:
            Deserialized arguments
        """
        defaulted_params = self._inspect_defaulted_parameters(func)
        result: DeserializedArgs = {}
        for param_name, param_value in args.items():
            deserialized = self._deserialize_param(
                param_name,
                param_value,
                type_hints,
                defaulted_params,
            )
            if isinstance(deserialized, _OmitArg):
                continue
            result[param_name] = deserialized
        return result

    def _deserialize_param(
        self,
        param_name: str,
        param_value: object,
        type_hints: TypeHints,
        defaulted_params: set[str],
    ) -> object | _OmitArg:
        """Deserialize a single parameter.

        Args:
            param_name: Parameter name
            param_value: Parameter value
            type_hints: Type hints for the function
            defaulted_params: Parameters with Python defaults

        Returns:
            Deserialized value, original value, or omit sentinel
        """
        annotation = type_hints.get(param_name)
        if annotation is None:
            return param_value
        if param_name in defaulted_params and self._is_defaultable_validation_type(annotation):
            validated = self._try_deserialize(param_value, annotation, param_name)
            if validated is None:
                if self._logger:
                    self._logger.debug(
                        "[DESERIALIZER] Dropping invalid value for defaulted parameter %s",
                        param_name,
                    )
                return _OMIT_ARG
            return validated
        return self._deserialize_value(param_value, annotation, param_name)

    @staticmethod
    def _inspect_defaulted_parameters(func: CallableObject) -> set[str]:
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            return set()
        return {
            name
            for name, param in sig.parameters.items()
            if param.default is not inspect.Parameter.empty
        }

    def _deserialize_value(
        self,
        value: object,
        annotation: object,
        param_name: str,
    ) -> object:
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

    @staticmethod
    def _is_pydantic_available() -> bool:
        """Check if Pydantic is available.

        Returns:
            True if Pydantic can be imported
        """
        return True

    @staticmethod
    def _is_union_type(annotation: object) -> bool:
        """Check if annotation is a Union type.

        Handles both typing.Union and Python 3.10+ X | Y syntax.

        Args:
            annotation: Type annotation to check

        Returns:
            True if annotation is a Union type
        """
        origin = get_origin(annotation)
        return origin is UnionType or str(origin) == "typing.Union"

    @staticmethod
    def _is_list_type(target_type: object) -> bool:
        """Check if target type is a list type.

        Args:
            target_type: Type to check

        Returns:
            True if target is list[T]
        """
        return get_origin(target_type) is list

    @staticmethod
    def _is_tuple_type(target_type: object) -> bool:
        """Check if target type is a tuple type."""
        return get_origin(target_type) is tuple

    @staticmethod
    def _is_primitive_type(target_type: object) -> bool:
        """Check if target type is a primitive JSON-compatible scalar."""
        return (
            target_type is str or target_type is int or target_type is float or target_type is bool
        )

    def _is_defaultable_validation_type(self, target_type: object) -> bool:
        """Check if invalid values can safely fall back to a function default."""
        if self._is_tuple_type(target_type) or self._is_primitive_type(target_type):
            return True
        return False

    @staticmethod
    def _is_pydantic_model(target_type: object) -> TypeGuard[type[BaseModel]]:
        """Check if target type is a Pydantic BaseModel subclass.

        Args:
            target_type: Type to check

        Returns:
            True if target is a Pydantic model
        """
        return isinstance(target_type, type) and issubclass(target_type, BaseModel)

    # MARK: - Union Handling

    def _handle_union(
        self,
        value: object,
        annotation: object,
        param_name: str,
    ) -> object:
        """Handle Union type deserialization.

        Tries each type in the Union until one succeeds.

        Args:
            value: Value to deserialize
            annotation: Union type annotation
            param_name: Parameter name (for logging)

        Returns:
            Deserialized value or original value if all types fail
        """
        for type_arg in cast(tuple[object, ...], get_args(annotation)):
            result = self._try_deserialize(value, type_arg, param_name)
            if result is not None:
                return result
        return value

    # MARK: - Type-Specific Deserialization

    def _try_deserialize(
        self,
        value: object,
        target_type: object,
        param_name: str,
    ) -> object | None:
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

        if self._is_tuple_type(target_type) or self._is_primitive_type(target_type):
            return self._validate_with_type_adapter(value, target_type)

        if self._is_pydantic_model(target_type):
            return self._deserialize_pydantic_model(value, target_type, param_name)

        return None

    def _validate_with_type_adapter(
        self,
        value: object,
        target_type: object,
    ) -> object | None:
        """Validate/coerce simple annotated values using Pydantic semantics."""
        try:
            return cast(object, TypeAdapter(target_type).validate_python(value))
        except (ValidationError, TypeError):
            return None

    def _deserialize_list(
        self,
        value: object,
        target_type: object,
    ) -> list[object] | None:
        """Deserialize list[Model] type.

        Args:
            value: Value to deserialize
            target_type: list[T] type

        Returns:
            Deserialized list or None if not applicable
        """
        if not isinstance(value, list):
            return None

        type_args = cast(tuple[object, ...], get_args(target_type))
        if not type_args:
            return None

        element_type = type_args[0]
        if not self._is_pydantic_model(element_type):
            return None

        items = cast(list[object], value)
        try:
            return [
                element_type(**cast(dict[str, object], item)) if isinstance(item, dict) else item
                for item in items
            ]
        except Exception as exc:  # noqa: BLE001 - one invalid item means leave raw list intact.
            if self._logger:
                self._logger.debug("[DESERIALIZER] Failed to deserialize list elements: %s", exc)
            return None

    def _deserialize_pydantic_model(
        self,
        value: object,
        target_type: type[BaseModel],
        param_name: str,
    ) -> BaseModel | None:
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

        self._ensure_model_rebuilt(target_type)

        try:
            return target_type(**cast(dict[str, object], value))
        except Exception as exc:  # noqa: BLE001 - invalid model input falls back to raw value.
            if self._logger:
                self._logger.debug(
                    "Could not convert %s to %s: %s",
                    param_name,
                    target_type.__name__,
                    str(exc),
                )
            return None

    def _ensure_model_rebuilt(self, target_type: type[BaseModel]) -> None:
        """Force ``model_rebuild()`` once per Pydantic class.

        Why this exists: tool callables defined in modules with
        ``from __future__ import annotations`` store every annotation as a
        string. Pydantic models with nested/recursive fields under those rules
        carry unresolved forward refs until ``model_rebuild()`` resolves them
        against the module namespace. The first instantiation otherwise raises
        ``PydanticUserError: '<Model>' is not fully defined``, the
        deserializer's broad exception guard preserves raw args, and the raw
        ``dict`` flows through to the function where attribute access fails.

        Rebuild is idempotent and cached by class object so the walk runs at
        most once per process per Pydantic model, regardless of how many tool
        calls reference it. ``force=True`` causes Pydantic to re-resolve even
        partially-built models; the call is a no-op for already-resolved
        classes.

        Critically, we pass ``_types_namespace=<model module globals>``.
        ``model_rebuild()`` defaults to walking the caller frames for names,
        but the caller here is the deserializer, not the user's module where
        the cross-referenced types live. Without explicit namespace passing,
        the rebuild fails with the same "not fully defined" error and the bug
        recurs.
        """
        import sys

        if target_type in self._rebuilt_models:
            return
        self._rebuilt_models.add(target_type)
        types_ns: dict[str, object] | None = None
        module_name = cast(object, getattr(target_type, "__module__", None))
        if isinstance(module_name, str) and module_name:
            module = sys.modules.get(module_name)
            if module is not None:
                types_ns = cast(dict[str, object], vars(module))
        try:
            if types_ns is not None:
                _ = target_type.model_rebuild(force=True, _types_namespace=types_ns)
            else:
                _ = target_type.model_rebuild(force=True)
        except Exception as exc:  # noqa: BLE001 - unresolved refs fall back to raw args.
            if self._logger:
                self._logger.debug(
                    "model_rebuild() failed for %s: %s",
                    target_type.__name__,
                    str(exc),
                )


# MARK: Module Helpers


def _callable_name(func: CallableObject) -> str:
    """Best-effort callable name for diagnostics."""
    name = getattr(func, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return func.__class__.__name__


# MARK: Public API

__all__ = ["PydanticDeserializer"]
