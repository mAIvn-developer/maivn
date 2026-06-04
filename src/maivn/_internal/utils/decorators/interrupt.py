# pyright: strict
"""Interrupt dependency decorator and annotation inference."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, cast, get_args, get_origin, get_type_hints

from maivn_shared import InterruptDependency
from maivn_shared.domain.entities.dependencies import InputType

from ._attach import (
    attach_dependency,
    attach_interrupt_team_dependency_if_supported,
    should_store_pending_team_dependency,
)
from ._introspection import validate_arg_in_signature
from ._types import F, InputHandler

# MARK: Public Decorators


def depends_on_interrupt(
    arg_name: str,
    input_handler: InputHandler,
    prompt: str = "",
    input_type: InputType | None = None,
    choices: list[str] | None = None,
) -> Callable[[F], F]:
    """Declare a dependency that interrupts execution for user input."""

    def decorator(func: F) -> F:
        if attach_interrupt_team_dependency_if_supported(
            func,
            arg_name=arg_name,
            input_handler=input_handler,
            prompt=prompt,
            input_type=input_type or "text",
            choices=choices or [],
        ):
            return func

        if should_store_pending_team_dependency(func, arg_name):
            dependency = InterruptDependency(
                arg_name=arg_name,
                prompt=prompt,
                input_handler=input_handler,
                input_type=input_type or "text",
                choices=choices or [],
            )
            attach_dependency(func, dependency)
            return func

        validate_arg_in_signature(func, arg_name)
        detected_type, detected_choices = detect_input_type_from_annotation(func, arg_name)
        final_type = input_type or detected_type
        final_choices = choices if choices is not None else detected_choices

        dependency = InterruptDependency(
            arg_name=arg_name,
            prompt=prompt,
            input_handler=input_handler,
            input_type=final_type,
            choices=final_choices,
        )
        attach_dependency(func, dependency)
        return func

    return decorator


# MARK: Annotation Detection


def detect_input_type_from_annotation(
    func: Callable[..., object],
    arg_name: str,
) -> tuple[InputType, list[str]]:
    """Detect interrupt input type and choices from a function parameter annotation."""
    try:
        hints = cast(Mapping[str, object], get_type_hints(func))
        if arg_name not in hints:
            return "text", []

        annotation = hints[arg_name]
        origin = get_origin(annotation)
        if origin is not None and origin is not Literal:
            none_type = type(None)
            args = [
                arg
                for arg in cast(tuple[object, ...], get_args(annotation))
                if arg is not none_type
            ]
            if len(args) == 1:
                annotation = args[0]

        origin = get_origin(annotation)
        if origin is Literal:
            literal_values = cast(tuple[object, ...], get_args(annotation))
            choices = [str(value) for value in literal_values]
            return "choice", choices

        if annotation is bool:
            return "boolean", []
        if annotation is int or annotation is float:
            return "number", []

        return "text", []
    except Exception:  # noqa: BLE001 - annotation inspection is best-effort.
        return "text", []


__all__ = [
    "depends_on_interrupt",
    "detect_input_type_from_annotation",
]
