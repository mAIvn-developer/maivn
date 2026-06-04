# pyright: strict
"""Public dependency decorators."""

from __future__ import annotations

from collections.abc import Callable

from maivn_shared import AgentDependency, DataDependency, ToolDependency, create_uuid

from ._attach import (
    attach_dependency,
    attach_team_dependency_if_supported,
    should_store_pending_team_dependency,
)
from ._introspection import (
    get_optional_str_attr,
    get_str_attr,
    instantiate_dependency,
    validate_arg_target,
)
from ._types import DependencyT, F, ToolReference

# MARK: Public Decorators


def depends_on_agent(agent_ref: object, arg_name: str) -> Callable[[F], F]:
    """Declare a dependency on another agent."""
    agent_id = get_str_attr(agent_ref, "agent_id", str(agent_ref))
    return create_dependency_decorator(
        dependency_model=AgentDependency,
        arg_name=arg_name,
        agent_id=agent_id,
    )


def depends_on_tool(tool_ref: ToolReference, arg_name: str) -> Callable[[F], F]:
    """Declare a dependency on another tool."""
    if callable(tool_ref):
        tool_id = get_optional_str_attr(tool_ref, "tool_id") or create_uuid(tool_ref)
    else:
        tool_id = get_str_attr(tool_ref, "tool_id", str(tool_ref))

    return create_dependency_decorator(
        dependency_model=ToolDependency,
        arg_name=arg_name,
        tool_id=tool_id,
    )


def depends_on_private_data(data_key: str, arg_name: str) -> Callable[[F], F]:
    """Declare a dependency on external private data."""
    return create_dependency_decorator(
        dependency_model=DataDependency,
        arg_name=arg_name,
        data_key=data_key,
    )


# MARK: Decorator Factory


def create_dependency_decorator(
    dependency_model: type[DependencyT],
    arg_name: str,
    **model_kwargs: object,
) -> Callable[[F], F]:
    """Create a dependency decorator with team, pending, and direct attach paths."""

    def decorator(func: F) -> F:
        if attach_team_dependency_if_supported(func, dependency_model, arg_name, model_kwargs):
            return func
        if should_store_pending_team_dependency(func, arg_name):
            dependency = instantiate_dependency(dependency_model, arg_name, model_kwargs)
            attach_dependency(func, dependency)
            return func

        validate_arg_target(
            func,
            arg_name,
            decorator_name="dependency decorator",
            walk_wrappers=True,
            include_model_fields=True,
        )
        dependency = instantiate_dependency(dependency_model, arg_name, model_kwargs)
        attach_dependency(func, dependency)
        return func

    return decorator


__all__ = [
    "create_dependency_decorator",
    "depends_on_agent",
    "depends_on_private_data",
    "depends_on_tool",
]
