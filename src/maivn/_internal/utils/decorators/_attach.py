# pyright: strict
"""Runtime metadata attachment and team-hook dispatch for decorators."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable
from typing import cast

from maivn_shared import BaseDependency, InterruptDependency
from maivn_shared.domain.entities.dependencies import (
    AwaitForDependency,
    InputType,
    ReevaluateDependency,
)
from pydantic import BaseModel

from ._introspection import (
    get_optional_str_attr,
    instantiate_dependency,
    returns_agent,
)
from ._types import ArgPolicy, DependencyT, InputHandler, ModelKwargs

# MARK: Configuration

LOGGER = logging.getLogger(__name__)


# MARK: Public Attach Helpers


def attach_dependency(func: Callable[..., object], dependency: BaseDependency) -> None:
    """Attach dependency metadata before or after tool creation."""
    append_runtime_item(func, "_dependencies", dependency)

    register_fn = cast(object, getattr(func, "__maivn_register_dependency__", None))
    if callable(register_fn):
        register_dependency = cast(Callable[[BaseDependency], object], register_fn)
        try:
            _ = register_dependency(dependency)
        except Exception as exc:  # noqa: BLE001 - dynamic callback failures must be logged.
            warn_registration_failure("dependency", func, exc)
    else:
        append_runtime_item(func, "__maivn_pending_deps__", dependency)


def attach_execution_control(
    func: Callable[..., object],
    control: AwaitForDependency | ReevaluateDependency,
) -> None:
    register_team_control = cast(object, getattr(func, "_add_team_execution_control", None))
    if callable(register_team_control):
        add_team_control = cast(
            Callable[[AwaitForDependency | ReevaluateDependency], object],
            register_team_control,
        )
        _ = add_team_control(control)
        return

    append_runtime_item(func, "__maivn_execution_controls__", control)

    register_fn = cast(object, getattr(func, "__maivn_register_execution_control__", None))
    if callable(register_fn):
        register_control = cast(
            Callable[[AwaitForDependency | ReevaluateDependency], object],
            register_fn,
        )
        try:
            _ = register_control(control)
        except Exception as exc:  # noqa: BLE001 - dynamic callback failures must be logged.
            warn_registration_failure("execution control", func, exc)
    else:
        append_runtime_item(func, "__maivn_pending_execution_controls__", control)


def attach_arg_policy(
    obj: Callable[..., object] | type[BaseModel],
    policy: ArgPolicy,
) -> None:
    append_runtime_item(obj, "__maivn_arg_policies__", policy)

    register_fn = cast(object, getattr(obj, "__maivn_register_arg_policy__", None))
    if callable(register_fn):
        register_policy = cast(Callable[[ArgPolicy], object], register_fn)
        try:
            _ = register_policy(policy)
        except Exception as exc:  # noqa: BLE001 - dynamic callback failures must be logged.
            warn_registration_failure("argument policy", obj, exc)
    else:
        append_runtime_item(obj, "__maivn_pending_arg_policies__", policy)


# MARK: Team-Hook Dispatch

# TODO(api-section): Replace duck-typed team hooks with a shared protocol when api owns it.


def attach_team_dependency_if_supported(
    target: object,
    dependency_model: type[DependencyT],
    arg_name: str,
    model_kwargs: ModelKwargs,
) -> bool:
    register_team_dependency = cast(object, getattr(target, "_add_team_dependency", None))
    if not callable(register_team_dependency):
        return False

    dependency = instantiate_dependency(dependency_model, arg_name, model_kwargs)
    add_team_dependency = cast(Callable[[BaseDependency], object], register_team_dependency)
    _ = add_team_dependency(dependency)
    return True


def attach_interrupt_team_dependency_if_supported(
    target: object,
    *,
    arg_name: str,
    input_handler: InputHandler,
    prompt: str,
    input_type: InputType,
    choices: list[str],
) -> bool:
    register_team_dependency = cast(object, getattr(target, "_add_team_dependency", None))
    if not callable(register_team_dependency):
        return False

    dependency = InterruptDependency(
        arg_name=arg_name,
        prompt=prompt,
        input_handler=input_handler,
        input_type=input_type,
        choices=choices,
    )
    add_team_dependency = cast(Callable[[BaseDependency], object], register_team_dependency)
    _ = add_team_dependency(dependency)
    return True


def should_store_pending_team_dependency(target: object, arg_name: str) -> bool:
    if not callable(target):
        return False
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return False
    if arg_name in signature.parameters:
        return False
    return returns_agent(signature)


# MARK: Utilities


def append_runtime_item(
    obj: object,
    attr_name: str,
    item: object,
) -> None:
    existing = cast(object | None, getattr(obj, attr_name, None))
    if existing is None:
        values: list[object] = []
    elif isinstance(existing, list):
        values = cast(list[object], existing)
    else:
        values = list(cast(Iterable[object], existing))

    values.append(item)
    setattr(obj, attr_name, values)


def warn_registration_failure(kind: str, target: object, exc: Exception) -> None:
    target_name = get_optional_str_attr(target, "__name__") or type(target).__name__
    LOGGER.warning(
        "Unable to register %s on %s; metadata remains attached for later discovery: %s",
        kind,
        target_name,
        exc,
        exc_info=True,
    )


__all__ = [
    "append_runtime_item",
    "attach_arg_policy",
    "attach_dependency",
    "attach_execution_control",
    "attach_interrupt_team_dependency_if_supported",
    "attach_team_dependency_if_supported",
    "should_store_pending_team_dependency",
    "warn_registration_failure",
]
