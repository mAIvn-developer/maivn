"""Compile a scope's tools into a graph: nested-model producer tools and their dependencies.

Split out of tooling.py.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    cast,
    get_args,
)

from pydantic import BaseModel

from maivn._internal.compat.decorators import (
    TOOL_DEPENDENCIES_ATTR,
    ToolDependency,
)
from maivn._internal.compat.tooling import (
    ToolRegistrationOptions,
    assign_tool_id,
    compile_tool_metadata,
    tool_metadata_for_callable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from maivn._internal.models import ToolMetadata


def compile_tool_metadata_graph(tools: Sequence[ToolMetadata]) -> list[ToolMetadata]:
    """Expand nested Pydantic models into executable dependency tools.

    Keep every nested model as a separate runtime unit, compile dependencies
    before their parents, and reuse explicitly registered metadata when a nested
    class was also toolified.
    """
    explicit_by_target = {id(tool.target): tool for tool in tools}
    explicit_by_name = {tool.name: tool for tool in tools}
    compiled: list[ToolMetadata] = []
    visited: set[int] = set()
    visiting: set[int] = set()
    direct_reference_counts = _direct_model_reference_counts(tools)

    def visit(tool: ToolMetadata) -> None:
        target_key = id(tool.target)
        if target_key in visited or target_key in visiting:
            return
        visiting.add(target_key)
        if _is_model_tool(tool.target):
            model = cast('type[BaseModel]', tool.target)
            for arg_name, nested_model, inject_directly in _nested_model_fields(model):
                dependency_tool = explicit_by_target.get(id(nested_model))
                if dependency_tool is None:
                    dependency_tool = tool_metadata_for_callable(
                        nested_model,
                        ToolRegistrationOptions(tags=tool.tags),
                    )
                    explicit_by_target[id(nested_model)] = dependency_tool
                    explicit_by_name[dependency_tool.name] = dependency_tool
                    assign_tool_id(nested_model, dependency_tool.name)
                # PARALLEL-BENCHMARK-NOTE(2026-08-16): Complex Types lost union
                # alternatives and injected one model into list/map fields. Register every
                # referenced model, but only a direct model-valued field is one dependency.
                if inject_directly and direct_reference_counts.get(id(nested_model)) == 1:
                    _append_model_dependency(model, arg_name, dependency_tool.name)
                visit(dependency_tool)
        for producer in _registered_tool_dependencies(tool, explicit_by_name):
            visit(producer)
        visiting.remove(target_key)
        visited.add(target_key)
        compiled.append(compile_tool_metadata(tool))

    for registered_tool in tools:
        visit(registered_tool)
    return compiled


def _registered_tool_dependencies(
    tool: ToolMetadata,
    tools_by_name: Mapping[str, ToolMetadata],
) -> list[ToolMetadata]:
    """Resolve the declared producers that participate in this compiled graph."""
    raw = getattr(tool.target, TOOL_DEPENDENCIES_ATTR, None)
    if not isinstance(raw, list):
        return []
    producers: list[ToolMetadata] = []
    for dependency in cast('list[object]', raw):
        if not isinstance(dependency, ToolDependency):
            continue
        producer = tools_by_name.get(dependency.tool_id) or tools_by_name.get(dependency.tool_name)
        if producer is not None:
            producers.append(producer)
    return producers


def _direct_model_reference_counts(tools: Sequence[ToolMetadata]) -> dict[int, int]:
    """Count direct model-valued fields across the reachable schema graph.

    The current runtime owns one dependency result per tool name. A model type used by
    several direct fields therefore cannot be inferred as a dependency: those fields
    represent distinct values even though they share one Pydantic class.
    """
    counts: dict[int, int] = {}
    visited: set[int] = set()
    pending = [tool.target for tool in tools if _is_model_tool(tool.target)]
    while pending:
        model = cast('type[BaseModel]', pending.pop())
        model_key = id(model)
        if model_key in visited:
            continue
        visited.add(model_key)
        for _arg_name, nested_model, inject_directly in _nested_model_fields(model):
            if inject_directly:
                nested_key = id(nested_model)
                counts[nested_key] = counts.get(nested_key, 0) + 1
            pending.append(nested_model)
    return counts


def _is_model_tool(target: object) -> bool:
    return isinstance(target, type) and issubclass(target, BaseModel)


def _nested_model_fields(model: type[BaseModel]) -> list[tuple[str, type[BaseModel], bool]]:
    nested: list[tuple[str, type[BaseModel], bool]] = []
    for arg_name, field_info in model.model_fields.items():
        candidates = _models_in_annotation(field_info.annotation)
        direct_model = (
            cast('type[BaseModel]', field_info.annotation)
            if _is_model_tool(field_info.annotation)
            else None
        )
        nested.extend((arg_name, candidate, candidate is direct_model) for candidate in candidates)
    return nested


def _models_in_annotation(annotation: object) -> list[type[BaseModel]]:
    if _is_model_tool(annotation):
        return [cast('type[BaseModel]', annotation)]
    found: list[type[BaseModel]] = []
    for argument in get_args(annotation):
        for model in _models_in_annotation(argument):
            if model not in found:
                found.append(model)
    return found


def _append_model_dependency(target: type[BaseModel], arg_name: str, dependency_name: str) -> None:
    raw = getattr(target, TOOL_DEPENDENCIES_ATTR, None)
    if raw is None:
        dependencies: list[object] = []
        setattr(target, TOOL_DEPENDENCIES_ATTR, dependencies)
    elif isinstance(raw, list):
        dependencies = cast('list[object]', raw)
    else:
        message = f'{TOOL_DEPENDENCIES_ATTR} metadata must be a list'
        raise TypeError(message)
    if any(isinstance(item, ToolDependency) and item.arg_name == arg_name for item in dependencies):
        return
    dependencies.append(ToolDependency(arg_name, dependency_name, dependency_name))


__all__ = [
    '_append_model_dependency',
    '_direct_model_reference_counts',
    '_is_model_tool',
    '_models_in_annotation',
    '_nested_model_fields',
    '_registered_tool_dependencies',
    'compile_tool_metadata_graph',
]
