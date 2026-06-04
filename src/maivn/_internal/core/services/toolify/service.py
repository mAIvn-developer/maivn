"""Toolify service for creating and registering tools from callables/models.

This module provides ToolifyService which encapsulates the heavy lifting
of the @toolify decorator, allowing BaseScope to remain thin.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, TypeAlias, TypedDict, TypeGuard, cast

from maivn_shared import BaseDependency
from pydantic import BaseModel

from maivn._internal.core.entities.tools import BaseTool, FunctionTool, MethodTool, ModelTool
from maivn._internal.core.services.dependency_collector import DependencyCollector

from .policies import (
    ComposeArtifactApproval,
    ComposeArtifactMode,
    collect_arg_policies,
    collect_execution_controls,
    merge_arg_policies,
    register_arg_policy_on_targets,
    register_dependency_on_targets,
    register_execution_control_on_targets,
)

if TYPE_CHECKING:
    from maivn._internal.core.interfaces.repositories import (
        DependencyRepoInterface,
    )
    from maivn._internal.core.registrars import ToolRegistrar


# MARK: Types

ToolifyTarget: TypeAlias = Callable[..., object] | type[BaseModel] | object
ToolHook: TypeAlias = Callable[[dict[str, object]], object]


class CommonToolKwargs(TypedDict):
    """Keyword arguments shared by all concrete tool models."""

    name: str
    description: str
    always_execute: bool
    final_tool: bool
    metadata: dict[str, object]
    tags: list[str]
    before_execute: ToolHook | None
    after_execute: ToolHook | None
    dependencies: list[BaseDependency]


# MARK: Toolify Options


class ToolifyOptions:
    """Configuration options for tool creation."""

    name: str | None
    description: str | None
    always_execute: bool
    final_tool: bool
    metadata: dict[str, object]
    tags: list[str]
    before_execute: ToolHook | None
    after_execute: ToolHook | None

    __slots__: tuple[str, ...] = (
        "name",
        "description",
        "always_execute",
        "final_tool",
        "metadata",
        "tags",
        "before_execute",
        "after_execute",
    )

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        always_execute: bool = False,
        final_tool: bool = False,
        metadata: dict[str, object] | None = None,
        tags: list[str] | None = None,
        before_execute: ToolHook | None = None,
        after_execute: ToolHook | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.always_execute = always_execute
        self.final_tool = final_tool
        self.metadata = dict(metadata or {})
        self.tags = tags or []
        self.before_execute = before_execute
        self.after_execute = after_execute


# MARK: Toolify Service


class ToolifyService:
    """Service for creating and registering tools from callables/models."""

    def __init__(
        self,
        dependency_collector: DependencyCollector | None = None,
    ) -> None:
        self._dependency_collector: DependencyCollector = (
            dependency_collector or DependencyCollector()
        )

    # MARK: - Public Methods

    def create_tool(
        self,
        obj: ToolifyTarget,
        options: ToolifyOptions,
    ) -> FunctionTool | ModelTool:
        """Create a tool from a callable or Pydantic model.

        Args:
            obj: Callable or Pydantic model class
            options: Tool creation options

        Returns:
            Created tool instance

        Raises:
            ValueError: If name or description is missing
            TypeError: If obj is not a callable or Pydantic model
        """
        common_kwargs = self._build_common_kwargs(obj, options)
        tool = self._create_tool_instance(obj, common_kwargs)
        tool.model_post_init(None)

        return tool

    def create_method_tool(
        self,
        bound_method: object,
        owner: object,
        options: ToolifyOptions,
        *,
        qualified_name: str | None = None,
    ) -> MethodTool:
        """Create a :class:`MethodTool` from a bound method of a toolset instance.

        Reuses the same name/description/metadata/dependency resolution path
        as :meth:`create_tool`, but produces a ``MethodTool`` with the
        ``owner`` reference attached.

        Args:
            bound_method: Bound method (``instance.method``). The runtime
                invokes this directly; ``self`` binding is preserved by the
                Python descriptor protocol.
            owner: The toolset instance that owns the method.
            options: Tool creation options (same shape as ``create_tool``).
            qualified_name: ``ClassName.method_name`` audit label. Defaults
                to a synthesized value from the owner's class.

        Raises:
            ValueError: If name or description cannot be resolved.
            TypeError: If ``bound_method`` is not callable.
        """
        if not callable(bound_method):
            raise TypeError("create_method_tool requires a callable bound method")

        method_func = bound_method
        common_kwargs = self._build_common_kwargs(method_func, options)
        method_name = cast(str, getattr(method_func, "__name__", "<method>"))
        qualified = qualified_name or f"{type(owner).__name__}.{method_name}"

        tool = MethodTool(
            **common_kwargs,
            func=method_func,
            owner=owner,
            qualified_name=qualified,
        )
        tool.model_post_init(None)
        return tool

    def register_tool(
        self,
        tool: BaseTool,
        registrar: ToolRegistrar,
        dependency_repo: DependencyRepoInterface,
    ) -> None:
        """Register a tool with repositories."""
        tool_id = tool.tool_id
        for dep in tool.dependencies:
            try:
                dependency_repo.add_dependency(tool_id, dep)
            except Exception:  # noqa: BLE001 - best-effort dependency registration.
                # Sibling deps still register on per-dep failure.
                continue
        registrar(tool)

    def setup_dependency_callback(
        self,
        obj: object,
        tool: BaseTool,
        dependency_repo: DependencyRepoInterface,
    ) -> None:
        """Set up dynamic dependency registration callback."""
        tool_id = tool.tool_id

        dep_callback = partial(
            register_dependency_on_targets,
            obj=obj,
            tool=tool,
            tool_id=tool_id,
            dependency_repo=dependency_repo,
        )
        _set_dynamic_attr(obj, "__maivn_register_dependency__", dep_callback)
        _set_dynamic_attr(tool, "__maivn_register_dependency__", dep_callback)

        control_callback = partial(register_execution_control_on_targets, obj=obj, tool=tool)
        _set_dynamic_attr(obj, "__maivn_register_execution_control__", control_callback)
        _set_dynamic_attr(tool, "__maivn_register_execution_control__", control_callback)

        policy_callback = partial(register_arg_policy_on_targets, obj=obj, tool=tool)
        _set_dynamic_attr(obj, "__maivn_register_arg_policy__", policy_callback)
        _set_dynamic_attr(tool, "__maivn_register_arg_policy__", policy_callback)

    # MARK: - Tool Creation Helpers

    def _build_common_kwargs(self, obj: ToolifyTarget, options: ToolifyOptions) -> CommonToolKwargs:
        """Build common keyword arguments for tool creation."""
        return {
            "name": self._resolve_name(obj, options.name),
            "description": self._resolve_description(obj, options.description),
            "always_execute": options.always_execute,
            "final_tool": options.final_tool,
            "metadata": self._build_metadata(obj, options.metadata),
            "tags": options.tags,
            "before_execute": options.before_execute,
            "after_execute": options.after_execute,
            "dependencies": self._dependency_collector.collect_all(obj),
        }

    def _build_metadata(
        self,
        obj: ToolifyTarget,
        base_metadata: dict[str, object] | None,
    ) -> dict[str, object]:
        """Build metadata dict with execution controls and arg policies."""
        metadata = dict(base_metadata or {})

        controls = collect_execution_controls(obj)
        if controls:
            execution_controls = metadata.setdefault("execution_controls", {})
            if not isinstance(execution_controls, dict):
                execution_controls = {}
                metadata["execution_controls"] = execution_controls
            execution_controls = cast(dict[str, object], execution_controls)

            for control_type, items in controls.items():
                existing = execution_controls.get(control_type, [])
                merged: list[object] = (
                    list(cast(list[object], existing)) if isinstance(existing, list) else []
                )
                for item in items:
                    if item not in merged:
                        merged.append(item)
                execution_controls[control_type] = merged

        arg_policies = collect_arg_policies(obj)
        if arg_policies:
            existing_arg_policies = metadata.get("arg_policies")
            merged_arg_policies = (
                merge_arg_policies(cast(object, existing_arg_policies))
                if isinstance(existing_arg_policies, dict)
                else {}
            )
            for arg_name, policy_map in arg_policies.items():
                current_map = merged_arg_policies.setdefault(arg_name, {})
                current_map.update(policy_map)
            metadata["arg_policies"] = merged_arg_policies

        return metadata

    def _create_tool_instance(
        self,
        obj: ToolifyTarget,
        common_kwargs: CommonToolKwargs,
    ) -> FunctionTool | ModelTool:
        """Create the appropriate tool instance based on object type."""
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            return ModelTool(**common_kwargs, model=obj)

        obj_candidate = cast(object, obj)
        if _is_callable_tool_target(obj_candidate):
            return FunctionTool(**common_kwargs, func=obj_candidate)

        raise TypeError(
            "Cannot create tool from object; must be callable or a Pydantic model class."
        )

    # MARK: - Resolution Helpers

    def _resolve_name(self, obj: ToolifyTarget, name: str | None) -> str:
        """Resolve tool name from object or provided value."""
        tool_name = name or cast(str | None, getattr(obj, "__name__", None))

        if not tool_name:
            raise ValueError(
                "Tool must have a name. Provide via 'name' or ensure object has __name__."
            )

        return tool_name

    def _resolve_description(self, obj: ToolifyTarget, description: str | None) -> str:
        """Resolve tool description from object docstring or provided value."""
        docstring = cast(str, getattr(obj, "__doc__", "") or "")
        tool_description = description or docstring.strip()

        if not tool_description:
            tool_name = cast(str, getattr(obj, "__name__", "<unknown>"))
            raise ValueError(
                f"Tool '{tool_name}' must have a description. Provide 'description' or docstring."
            )

        return tool_description


# MARK: Helpers


def _is_callable_tool_target(obj: object) -> TypeGuard[Callable[..., object]]:
    """Return whether an object can be wrapped as a callable tool."""
    return callable(obj)


def _set_dynamic_attr(target: object, attr_name: str, value: object) -> None:
    """Set a decorator-attached dynamic attribute."""
    setattr(target, attr_name, value)


__all__ = [
    "ComposeArtifactApproval",
    "ComposeArtifactMode",
    "DependencyCollector",
    "ToolifyOptions",
    "ToolifyService",
]
