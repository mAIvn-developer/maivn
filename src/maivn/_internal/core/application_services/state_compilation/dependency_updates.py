"""ToolSpec dependency reference updates for state compilation."""

# pyright: strict
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TypeAlias, cast

from maivn_shared import ToolSpec
from pydantic import JsonValue

from maivn._internal.core.entities import BaseTool

# MARK: - Types

JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True)
class _DependencyTarget:
    tool_id: str
    tool_name: str


# MARK: - Public API


def update_tool_dependency_references(
    tool_specs: list[ToolSpec],
    all_tools: list[BaseTool],
) -> None:
    """Canonicalize tool_dependency refs to tool UUIDs and refresh name snapshots.

    String-authored dependencies may enter the schema with ``tool_id`` carrying a
    tool name. Resolve those names against the compiled tool set so persisted and
    runtime schemas use canonical tool IDs. ``tool_name`` remains a display/debug
    snapshot and a temporary fallback for legacy rows.
    """
    targets_by_id, targets_by_name = _build_dependency_target_maps(tool_specs, all_tools)

    for spec in tool_specs:
        _update_spec_dependencies(spec, targets_by_id, targets_by_name)


def deduplicate_tool_specs(specs: list[ToolSpec]) -> list[ToolSpec]:
    """Remove duplicate ToolSpecs while preserving order."""
    if not specs:
        return []

    ordered_specs: OrderedDict[str, ToolSpec] = OrderedDict()
    for spec in specs:
        if spec.tool_id not in ordered_specs:
            ordered_specs[spec.tool_id] = spec
    return list(ordered_specs.values())


# MARK: - Dependency Mapping Helpers


def _build_dependency_target_maps(
    tool_specs: list[ToolSpec],
    all_tools: list[BaseTool],
) -> tuple[dict[str, _DependencyTarget], dict[str, list[_DependencyTarget]]]:
    targets_by_id: dict[str, _DependencyTarget] = {}
    targets_by_name: dict[str, list[_DependencyTarget]] = {}

    def add_target(tool_id: object, tool_name: object) -> None:
        if not (isinstance(tool_id, str) and tool_id and isinstance(tool_name, str) and tool_name):
            return
        target = _DependencyTarget(tool_id=tool_id, tool_name=tool_name)
        targets_by_id.setdefault(tool_id, target)

        named_targets = targets_by_name.setdefault(tool_name, [])
        if all(existing.tool_id != tool_id for existing in named_targets):
            named_targets.append(target)

    for tool in all_tools:
        add_target(getattr(tool, "tool_id", None), getattr(tool, "name", None))
    for spec in tool_specs:
        add_target(spec.tool_id, spec.name)

    return targets_by_id, targets_by_name


def _update_spec_dependencies(
    spec: ToolSpec,
    targets_by_id: dict[str, _DependencyTarget],
    targets_by_name: dict[str, list[_DependencyTarget]],
) -> None:
    if not isinstance(spec.args_schema, dict):
        return

    _update_schema_recursive(spec.args_schema, targets_by_id, targets_by_name)


def _update_schema_recursive(
    schema: JsonObject,
    targets_by_id: dict[str, _DependencyTarget],
    targets_by_name: dict[str, list[_DependencyTarget]],
) -> None:
    if schema.get("type") == "tool_dependency":
        _canonicalize_dependency_schema(schema, targets_by_id, targets_by_name)
        return

    for key, value in list(schema.items()):
        value_object = _as_json_object(value)
        if key == "items" and value_object is not None:
            _update_schema_recursive(value_object, targets_by_id, targets_by_name)
        elif key == "additionalProperties" and value_object is not None:
            _update_schema_recursive(value_object, targets_by_id, targets_by_name)
        elif key in ("anyOf", "oneOf") and isinstance(value, list):
            for variant in cast(list[JsonValue], value):
                variant_object = _as_json_object(variant)
                if variant_object is not None:
                    _update_schema_recursive(variant_object, targets_by_id, targets_by_name)
        elif value_object is not None:
            _update_schema_recursive(value_object, targets_by_id, targets_by_name)


def _canonicalize_dependency_schema(
    schema: JsonObject,
    targets_by_id: dict[str, _DependencyTarget],
    targets_by_name: dict[str, list[_DependencyTarget]],
) -> None:
    dep_tool_id = _optional_string(schema.get("tool_id"))
    dep_tool_name = _optional_string(schema.get("tool_name"))

    target = targets_by_id.get(dep_tool_id or "")
    if target is None:
        target = _resolve_by_name(dep_tool_id or dep_tool_name, targets_by_name)
    if target is None and dep_tool_name is not None:
        target = _resolve_by_name(dep_tool_name, targets_by_name)
    if target is None:
        return

    schema["tool_id"] = target.tool_id
    schema["tool_name"] = target.tool_name


def _resolve_by_name(
    name: str | None,
    targets_by_name: dict[str, list[_DependencyTarget]],
) -> _DependencyTarget | None:
    if not name:
        return None

    targets = targets_by_name.get(name)
    if not targets:
        return None
    if len(targets) > 1:
        target_ids = sorted(target.tool_id for target in targets)
        raise ValueError(
            f"Ambiguous tool dependency reference '{name}': "
            + f"multiple tools in the compiled tool set share that name ({target_ids})"
        )
    return targets[0]


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_json_object(value: object) -> JsonObject | None:
    if isinstance(value, dict):
        return cast(JsonObject, value)
    return None


__all__ = ["deduplicate_tool_specs", "update_tool_dependency_references"]
