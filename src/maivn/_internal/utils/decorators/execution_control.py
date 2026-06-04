# pyright: strict
"""Execution-control decorators for dependency ordering and replanning."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from maivn_shared.domain.entities.dependencies import (
    AwaitForDependency,
    ExecutionInstanceControl,
    ExecutionTiming,
    ReevaluateDependency,
)

from ._attach import attach_execution_control
from ._introspection import resolve_tool_reference
from ._types import F, ToolReference

# MARK: Public Decorators


def depends_on_await_for(
    tool_ref: ToolReference,
    *,
    timing: ExecutionTiming = "after",
    instance_control: ExecutionInstanceControl = "each",
) -> Callable[[F], F]:
    """Block the decorated tool until the referenced tool finishes."""
    return create_execution_control_decorator(
        control_model=AwaitForDependency,
        tool_ref=tool_ref,
        timing=timing,
        instance_control=instance_control,
    )


def depends_on_reevaluate(
    tool_ref: ToolReference,
    *,
    timing: ExecutionTiming = "after",
    instance_control: ExecutionInstanceControl = "each",
) -> Callable[[F], F]:
    """Trigger a re-planning cycle relative to the referenced tool."""
    return create_execution_control_decorator(
        control_model=ReevaluateDependency,
        tool_ref=tool_ref,
        timing=timing,
        instance_control=instance_control,
    )


# MARK: Decorator Factory


def create_execution_control_decorator(
    control_model: type[AwaitForDependency] | type[ReevaluateDependency],
    tool_ref: ToolReference,
    *,
    timing: ExecutionTiming,
    instance_control: ExecutionInstanceControl,
) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        resolver = cast(object, getattr(func, "_resolve_team_control_reference", None))
        if callable(resolver):
            resolve_team_control = cast(Callable[[ToolReference], tuple[str, str]], resolver)
            tool_id, tool_name = resolve_team_control(tool_ref)
        else:
            tool_id, tool_name = resolve_tool_reference(tool_ref)

        control = control_model(
            tool_id=tool_id,
            tool_name=tool_name,
            timing=timing,
            instance_control=instance_control,
        )
        attach_execution_control(func, control)
        return func

    return decorator


__all__ = [
    "create_execution_control_decorator",
    "depends_on_await_for",
    "depends_on_reevaluate",
]
