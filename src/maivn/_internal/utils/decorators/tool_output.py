"""Public decorator for declaring function-tool output schemas."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import TypeGuard, cast

from pydantic import BaseModel

from maivn._internal.core.output_schema import OutputSchemaInput, attach_output_schema

from ._types import F

# MARK: Public Decorator


def tool_output(schema: OutputSchemaInput) -> Callable[[F], F]:
    """Declare the JSON schema for a function or method tool's result."""
    normalized_decorator_schema = schema

    def decorator(func: F) -> F:
        target = cast(object, func)
        if _is_pydantic_model_class(target):
            raise TypeError(
                "tool_output is not supported for model tools; model tools derive "
                "their contract from the Pydantic model class."
            )
        attach_output_schema(target, normalized_decorator_schema)
        return func

    return decorator


def _is_pydantic_model_class(value: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(value, type) and issubclass(value, BaseModel)


__all__ = ["tool_output"]
