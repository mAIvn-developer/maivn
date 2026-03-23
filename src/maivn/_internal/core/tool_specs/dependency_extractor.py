"""Dependency extraction and arg policy application for tool schemas.

Provides utilities for extracting tool dependency information from schemas
and applying compose artifact policies to schema properties.
"""

from __future__ import annotations

from typing import Any

# MARK: Dependency Extraction


def extract_tool_dependencies(args_schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool dependencies from a schema.

    Args:
        args_schema: The tool's args_schema

    Returns:
        List of tool dependency information
    """
    dependencies: list[dict[str, Any]] = []
    _extract_dependencies_recursive(args_schema.get("properties", {}), dependencies)
    return dependencies


def _extract_dependencies_recursive(
    schema: Any,
    dependencies: list[dict[str, Any]],
    property_name: str = "",
) -> None:
    """Recursively extract tool dependencies from schema."""
    if not isinstance(schema, dict):
        return

    if schema.get("type") == "tool_dependency":
        dependencies.append(
            {
                "tool_id": schema["tool_id"],
                "tool_name": schema["tool_name"],
                "tool_type": schema["tool_type"],
                "property_name": property_name,
                "output_type": schema.get("output_type", "object"),
                "description": schema.get("description", ""),
            }
        )
        return

    for key, value in schema.items():
        if key == "items":
            _extract_dependencies_recursive(value, dependencies, f"{property_name}[]")
        elif key == "additionalProperties":
            _extract_dependencies_recursive(value, dependencies, f"{property_name}[*]")
        elif key in ("anyOf", "oneOf") and isinstance(value, list):
            for variant in value:
                _extract_dependencies_recursive(variant, dependencies, property_name)
        elif isinstance(value, dict):
            _extract_dependencies_recursive(value, dependencies, property_name)


# MARK: Arg Policy Application


def apply_arg_policies_to_schema(schema: Any, metadata: dict[str, Any]) -> None:
    """Apply arg policies from metadata to schema properties.

    Reads compose_artifact policies from metadata['arg_policies'] and
    annotates matching schema properties with the normalized policy.
    """
    if not isinstance(schema, dict):
        return
    arg_policies = metadata.get("arg_policies")
    if not isinstance(arg_policies, dict):
        return

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return

    for arg_name, policy_map in arg_policies.items():
        if not isinstance(arg_name, str) or not isinstance(policy_map, dict):
            continue
        prop_schema = properties.get(arg_name)
        if not isinstance(prop_schema, dict):
            continue

        _apply_compose_policy(prop_schema, policy_map)


def _apply_compose_policy(
    prop_schema: dict[str, Any],
    policy_map: dict[str, Any],
) -> None:
    """Apply a single compose_artifact policy to a property schema."""
    compose_policy = policy_map.get("compose_artifact")
    if not isinstance(compose_policy, dict):
        return

    mode = compose_policy.get("mode")
    approval = compose_policy.get("approval", "none")
    if mode not in {"forbid", "allow", "require"}:
        return
    if approval not in {"none", "explicit"}:
        approval = "none"

    normalized_policy = {"mode": mode, "approval": approval}
    prop_schema["compose_artifact_policy"] = normalized_policy

    summary = f"Compose artifact policy: mode={mode}, approval={approval}."
    description = prop_schema.get("description")
    if isinstance(description, str) and description.strip():
        if summary not in description:
            prop_schema["description"] = f"{description.rstrip()} {summary}"
    else:
        prop_schema["description"] = summary


# MARK: Metadata Merging


def merge_metadata(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Deep-merge incoming metadata into current metadata.

    Dict values are recursively merged, list values are deduplicated,
    and scalar values from incoming overwrite current.
    """
    merged = dict(current or {})
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_metadata(merged.get(key), value)
            continue
        if key not in merged:
            merged[key] = value
            continue
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = list(dict.fromkeys([*merged[key], *value]))
            continue
        merged[key] = value
    return merged


__all__ = [
    "apply_arg_policies_to_schema",
    "extract_tool_dependencies",
    "merge_metadata",
]
