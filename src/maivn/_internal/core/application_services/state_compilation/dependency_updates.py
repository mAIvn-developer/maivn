"""ToolSpec dependency reference updates for state compilation."""

from __future__ import annotations

from collections import OrderedDict

from maivn_shared import ToolSpec

from maivn._internal.core.entities import BaseTool

# MARK: - Public API


def update_tool_dependency_references(
    tool_specs: list[ToolSpec],
    all_tools: list[BaseTool],
) -> None:
    """Update tool_dependency references in ToolSpecs to use actual tool names."""
    tool_id_to_name = _build_tool_id_map(tool_specs, all_tools)

    for spec in tool_specs:
        _update_spec_dependencies(spec, tool_id_to_name)


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


def _build_tool_id_map(
    tool_specs: list[ToolSpec],
    all_tools: list[BaseTool],
) -> dict[str, str]:
    tool_id_to_name = {tool.tool_id: tool.name for tool in all_tools}
    for spec in tool_specs:
        tool_id_to_name[spec.tool_id] = spec.name
    return tool_id_to_name


def _update_spec_dependencies(spec: ToolSpec, tool_id_to_name: dict[str, str]) -> None:
    if not hasattr(spec, "args_schema") or not isinstance(spec.args_schema, dict):
        return

    properties = spec.args_schema.get("properties", {})
    for prop_schema in properties.values():
        if isinstance(prop_schema, dict):
            _update_schema_recursive(prop_schema, tool_id_to_name)


def _update_schema_recursive(schema: dict, tool_id_to_name: dict[str, str]) -> None:
    if not isinstance(schema, dict):
        return

    if schema.get("type") == "tool_dependency":
        dep_tool_id = schema.get("tool_id")
        if dep_tool_id and dep_tool_id in tool_id_to_name:
            schema["tool_name"] = tool_id_to_name[dep_tool_id]
        return

    for key, value in schema.items():
        if key == "items" and isinstance(value, dict):
            _update_schema_recursive(value, tool_id_to_name)
        elif key == "additionalProperties" and isinstance(value, dict):
            _update_schema_recursive(value, tool_id_to_name)
        elif key in ("anyOf", "oneOf") and isinstance(value, list):
            for variant in value:
                if isinstance(variant, dict):
                    _update_schema_recursive(variant, tool_id_to_name)
        elif isinstance(value, dict):
            _update_schema_recursive(value, tool_id_to_name)


__all__ = ["deduplicate_tool_specs", "update_tool_dependency_references"]
