"""Dependency extraction and arg policy application for tool schemas.

Provides utilities for extracting tool dependency information from schemas
and applying compose artifact policies to schema properties.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal, TypeAlias, cast

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]

ComposeMode = Literal["forbid", "allow", "require"]
ComposeApproval = Literal["none", "explicit"]

# MARK: Constants

_COMPOSE_MODES: Final[frozenset[ComposeMode]] = cast(
    frozenset[ComposeMode],
    frozenset(("forbid", "allow", "require")),
)  # pyright: ignore[reportUnnecessaryCast]
_COMPOSE_APPROVALS: Final[frozenset[ComposeApproval]] = cast(
    frozenset[ComposeApproval],
    frozenset(("none", "explicit")),
)  # pyright: ignore[reportUnnecessaryCast]

# MARK: Dependency Extraction


def extract_tool_dependencies(args_schema: JsonObject) -> list[JsonObject]:
    """Extract tool dependencies from a schema.

    Args:
        args_schema: The tool's args_schema

    Returns:
        List of tool dependency information
    """
    dependencies: list[JsonObject] = []
    _extract_dependencies_recursive(args_schema.get("properties", {}), dependencies)
    return dependencies


def _extract_dependencies_recursive(
    schema: object,
    dependencies: list[JsonObject],
    property_name: str = "",
) -> None:
    """Recursively extract tool dependencies from schema."""
    schema_obj = _as_json_object(schema)
    if schema_obj is None:
        return

    if schema_obj.get("type") == "tool_dependency":
        dependencies.append(
            {
                "tool_id": schema_obj["tool_id"],
                "tool_name": schema_obj["tool_name"],
                "tool_type": schema_obj["tool_type"],
                "property_name": property_name,
                "output_type": schema_obj.get("output_type", "object"),
                "description": schema_obj.get("description", ""),
            }
        )
        return

    for key, value in schema_obj.items():
        if key == "items":
            _extract_dependencies_recursive(value, dependencies, f"{property_name}[]")
        elif key == "additionalProperties":
            _extract_dependencies_recursive(value, dependencies, f"{property_name}[*]")
        elif key in ("anyOf", "oneOf") and isinstance(value, list):
            for variant in value:
                _extract_dependencies_recursive(variant, dependencies, property_name)
        elif _as_json_object(value) is not None:
            _extract_dependencies_recursive(value, dependencies, property_name)


# MARK: Arg Policy Application


def apply_arg_policies_to_schema(schema: object, metadata: Mapping[str, JsonValue]) -> None:
    """Apply arg policies from metadata to schema properties.

    Reads compose_artifact policies from metadata['arg_policies'] and
    annotates matching schema properties with the normalized policy.
    """
    schema_obj = _as_json_object(schema)
    if schema_obj is None:
        return

    arg_policies = _as_json_object(metadata.get("arg_policies"))
    if arg_policies is None:
        return

    properties = _as_json_object(schema_obj.get("properties"))
    if properties is None:
        return

    for arg_name, policy_map in arg_policies.items():
        policy_map_obj = _as_json_object(policy_map)
        if policy_map_obj is None:
            continue
        prop_schema = properties.get(arg_name)
        prop_schema_obj = _as_json_object(prop_schema)
        if prop_schema_obj is None:
            continue

        _apply_compose_policy(prop_schema_obj, policy_map_obj)


def _apply_compose_policy(
    prop_schema: JsonObject,
    policy_map: JsonObject,
) -> None:
    """Apply a single compose_artifact policy to a property schema."""
    compose_policy = _as_json_object(policy_map.get("compose_artifact"))
    if compose_policy is None:
        return

    mode = _get_allowed_string(compose_policy.get("mode"), _COMPOSE_MODES)
    if mode is None:
        return

    approval = _get_allowed_string(compose_policy.get("approval"), _COMPOSE_APPROVALS)
    if approval is None:
        approval = "none"

    normalized_policy: JsonObject = {"mode": mode, "approval": approval}
    prop_schema["compose_artifact_policy"] = normalized_policy

    summary = f"Compose artifact policy: mode={mode}, approval={approval}."
    description_raw = prop_schema.get("description")
    if isinstance(description_raw, str):
        description: str = description_raw
        if description.strip():
            if summary not in description:
                prop_schema["description"] = f"{description.rstrip()} {summary}"
        else:
            prop_schema["description"] = summary
    else:
        prop_schema["description"] = summary


# MARK: Metadata Merging


def merge_metadata(
    current: JsonObject | None,
    incoming: JsonObject | None,
) -> JsonObject:
    """Deep-merge incoming metadata into current metadata.

    Dict values are recursively merged, list values are deduplicated,
    and scalar values from incoming overwrite current.
    """
    merged: JsonObject = dict(current or {})
    for key, value in (incoming or {}).items():
        current_value = merged.get(key)
        value_obj = _as_json_object(value)
        current_obj = _as_json_object(current_value)
        if value_obj is not None and current_obj is not None:
            merged[key] = merge_metadata(current_obj, value_obj)
            continue
        if key not in merged:
            merged[key] = value
            continue
        if isinstance(value, list) and isinstance(current_value, list):
            merged[key] = _dedupe_preserving_order([*current_value, *value])
            continue
        merged[key] = value
    return merged


# MARK: Helpers


def _dedupe_preserving_order(items: list[JsonValue]) -> list[JsonValue]:
    """Drop duplicate list elements, preserving first-seen order.

    Hashable elements are deduplicated via a ``set`` (O(1) membership); valid
    JSON list values can also contain ``dict`` / ``list`` elements, which are
    unhashable. Those fall back to equality comparison against the elements
    already kept. This preserves the prior hashable-list semantics exactly
    (order-preserving, first occurrence wins) without raising ``TypeError`` on
    unhashable JSON values.
    """
    result: list[JsonValue] = []
    seen_hashable: set[object] = set()
    seen_unhashable: list[JsonValue] = []
    for item in items:
        try:
            if item in seen_hashable:
                continue
            seen_hashable.add(item)
        except TypeError:
            if item in seen_unhashable:
                continue
            seen_unhashable.append(item)
        result.append(item)
    return result


def _as_json_object(value: object) -> JsonObject | None:
    """Return a JSON object view for dict-shaped values."""
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


def _get_allowed_string(value: JsonValue | None, allowed: frozenset[str]) -> str | None:
    """Return a string only when it is part of an allowed vocabulary."""
    if isinstance(value, str) and value in allowed:
        return value
    return None


__all__ = [
    "apply_arg_policies_to_schema",
    "extract_tool_dependencies",
    "merge_metadata",
]
