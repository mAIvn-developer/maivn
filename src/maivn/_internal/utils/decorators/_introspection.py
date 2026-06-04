# pyright: strict
"""Signature and reference helpers for dependency decorators."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import cast

from maivn_shared import create_uuid
from pydantic import BaseModel

from ._types import DependencyT, ModelKwargs, ToolReference

# MARK: Attribute Helpers


def get_optional_str_attr(obj: object, attr_name: str) -> str | None:
    value = cast(object | None, getattr(obj, attr_name, None))
    return str(value) if value is not None else None


def get_str_attr(obj: object, attr_name: str, default: str) -> str:
    return get_optional_str_attr(obj, attr_name) or default


# MARK: Dependency Construction


def instantiate_dependency(
    dependency_model: type[DependencyT],
    arg_name: str,
    model_kwargs: ModelKwargs,
) -> DependencyT:
    dependency_factory = cast(Callable[..., DependencyT], dependency_model)
    return dependency_factory(arg_name=arg_name, **model_kwargs)


# MARK: Validation


def validate_arg_in_signature(
    func: Callable[..., object],
    arg_name: str,
    *,
    decorator_name: str = "dependency decorator",
) -> None:
    signature = inspect.signature(func)
    if arg_name not in signature.parameters:
        func_name = get_str_attr(func, "__name__", "<function>")
        raise ValueError(
            f"Argument '{arg_name}' specified in {decorator_name} "
            + f"not found in function '{func_name}' signature: {signature}"
        )


def validate_arg_target(
    obj: object,
    arg_name: str,
    *,
    decorator_name: str,
    walk_wrappers: bool,
    include_model_fields: bool,
    signature_decorator_name: str = "dependency decorator",
) -> None:
    target = resolve_decorator_target(obj) if walk_wrappers else obj

    if inspect.isclass(target) and issubclass(target, BaseModel):
        model_fields = cast(Mapping[str, object], getattr(target, "model_fields", {}))
        if arg_name not in model_fields:
            model_name = get_str_attr(target, "__name__", "<model>")
            field_suffix = f" fields: {list(model_fields)}" if include_model_fields else " fields"
            raise ValueError(
                f"Argument '{arg_name}' specified in {decorator_name} "
                + f"not found in model '{model_name}'{field_suffix}"
            )
        return

    validate_arg_in_signature(
        cast(Callable[..., object], target),
        arg_name,
        decorator_name=signature_decorator_name,
    )


def resolve_decorator_target(obj: object) -> object:
    """Walk one known wrapper layer to find the original target."""
    for attr in ("_maivn_target_obj", "__wrapped__"):
        candidate = cast(object | None, getattr(obj, attr, None))
        if candidate is not None:
            return candidate
    return obj


# MARK: Reference Resolution


def returns_agent(signature: inspect.Signature) -> bool:
    annotation = cast(object, signature.return_annotation)
    if annotation == inspect.Signature.empty:
        return False
    if isinstance(annotation, str):
        return annotation == "Agent" or annotation.endswith(".Agent")
    return get_optional_str_attr(annotation, "__name__") == "Agent"


def resolve_tool_reference(tool_ref: ToolReference) -> tuple[str, str]:
    if callable(tool_ref):
        tool_id = get_optional_str_attr(tool_ref, "tool_id") or create_uuid(tool_ref)
        tool_name = (
            get_optional_str_attr(tool_ref, "name")
            or get_optional_str_attr(tool_ref, "__name__")
            or str(tool_ref)
        )
        return str(tool_id), str(tool_name)

    tool_id = get_str_attr(tool_ref, "tool_id", str(tool_ref))
    tool_name = get_optional_str_attr(tool_ref, "name") or str(tool_ref)
    return str(tool_id), str(tool_name)


__all__ = [
    "get_optional_str_attr",
    "get_str_attr",
    "instantiate_dependency",
    "resolve_decorator_target",
    "resolve_tool_reference",
    "returns_agent",
    "validate_arg_in_signature",
    "validate_arg_target",
]
