"""Type resolution utilities for tool specification generation.

Provides safe type resolution helpers for forward references and string annotations
without using eval(). Also includes Pydantic model detection utilities.
"""

from __future__ import annotations

import builtins
import inspect
import sys
import typing
from typing import Any, ForwardRef, get_args, get_origin

from pydantic import BaseModel

# MARK: Type Resolution


def safe_resolve_string_type(
    type_str: str,
    module_globals: dict[str, Any],
    module_locals: dict[str, Any] | None = None,
) -> type | None:
    """Safely resolve a string type annotation without using eval().

    Args:
        type_str: The type name as a string
        module_globals: Globals from the defining module
        module_locals: Optional locals for resolution

    Returns:
        The resolved type, or None if resolution fails
    """
    if type_str in module_globals:
        return module_globals[type_str]

    if module_locals and type_str in module_locals:
        return module_locals[type_str]

    if "." in type_str:
        resolved = resolve_dotted_name(type_str, module_globals, module_locals)
        if resolved is not None:
            return resolved

    if hasattr(builtins, type_str):
        return getattr(builtins, type_str)

    return None


def resolve_dotted_name(
    type_str: str,
    module_globals: dict[str, Any],
    module_locals: dict[str, Any] | None,
) -> type | None:
    """Resolve a dotted name like 'module.ClassName'.

    Args:
        type_str: The dotted type name
        module_globals: Globals from the defining module
        module_locals: Optional locals for resolution

    Returns:
        The resolved type, or None if resolution fails
    """
    parts = type_str.split(".")
    obj = module_globals.get(parts[0])

    if obj is None and module_locals:
        obj = module_locals.get(parts[0])

    if obj is None:
        return None

    try:
        for part in parts[1:]:
            obj = getattr(obj, part)
        return obj
    except AttributeError:
        return None


def resolve_forward_ref(
    ref: ForwardRef | str,
    module_globals: dict[str, Any],
    module_locals: dict[str, Any] | None = None,
) -> type | None:
    """Resolve a ForwardRef or string annotation safely.

    Args:
        ref: The forward reference or string to resolve
        module_globals: Globals from the defining module
        module_locals: Optional locals for resolution

    Returns:
        The resolved type, or None if resolution fails
    """
    type_str = ref.__forward_arg__ if isinstance(ref, ForwardRef) else ref
    return safe_resolve_string_type(type_str, module_globals, module_locals)


# MARK: Pydantic Model Detection


def is_pydantic_model(annotation: Any) -> bool:
    """Check if an annotation is a Pydantic model class.

    Args:
        annotation: The type annotation to check

    Returns:
        True if the annotation is a Pydantic BaseModel subclass
    """
    try:
        return isinstance(annotation, type) and issubclass(annotation, BaseModel)
    except TypeError:
        return False


def extract_nested_models(field_type: Any) -> list[type[BaseModel]]:
    """Extract Pydantic model classes from a field type annotation.

    Recursively inspects Union types and generic containers to find
    all nested Pydantic models.

    Args:
        field_type: The type annotation to analyze

    Returns:
        List of Pydantic model classes found in the annotation
    """
    models: list[type[BaseModel]] = []

    if isinstance(field_type, str):
        return models

    if is_pydantic_model(field_type):
        models.append(field_type)
        return models

    origin = get_origin(field_type)
    if origin is typing.Union or origin is not None:
        for arg in get_args(field_type):
            models.extend(extract_nested_models(arg))

    return models


# MARK: Module Globals Helpers


def get_module_globals_for_callable(func: Any) -> dict[str, Any]:
    """Get module globals for a callable.

    Args:
        func: The callable to get globals for

    Returns:
        Dictionary of module globals
    """
    func_module = inspect.getmodule(func)
    return func_module.__dict__ if func_module else {}


def get_module_globals_for_model(model: type[BaseModel]) -> dict[str, Any]:
    """Get module globals for a Pydantic model class.

    Args:
        model: The model class to get globals for

    Returns:
        Dictionary of module globals
    """
    model_module = model.__module__
    module = sys.modules.get(model_module)
    return module.__dict__ if module else {}


__all__ = [
    "extract_nested_models",
    "get_module_globals_for_callable",
    "get_module_globals_for_model",
    "is_pydantic_model",
    "resolve_dotted_name",
    "resolve_forward_ref",
    "safe_resolve_string_type",
]
