from __future__ import annotations

from maivn._internal.core.tool_specs.dependency_extractor import (
    apply_arg_policies_to_schema,
    extract_tool_dependencies,
    merge_metadata,
)

# MARK: extract_tool_dependencies


def test_extract_returns_empty_for_no_properties() -> None:
    result = extract_tool_dependencies({})
    assert result == []


def test_extract_skips_non_dict_schema() -> None:
    """Covers line 35: early return when schema is not a dict."""
    schema = {
        "properties": {
            "field": "not-a-dict",
        },
    }
    result = extract_tool_dependencies(schema)
    assert result == []


def test_extract_direct_dependency() -> None:
    schema = {
        "properties": {
            "source": {
                "type": "tool_dependency",
                "tool_id": "tid-1",
                "tool_name": "fetch",
                "tool_type": "func",
                "output_type": "string",
                "description": "Fetch data",
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert len(deps) == 1
    assert deps[0]["tool_id"] == "tid-1"
    assert deps[0]["output_type"] == "string"
    assert deps[0]["description"] == "Fetch data"


def test_extract_dependency_defaults_output_type_and_description() -> None:
    schema = {
        "properties": {
            "source": {
                "type": "tool_dependency",
                "tool_id": "tid-2",
                "tool_name": "parse",
                "tool_type": "model",
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert deps[0]["output_type"] == "object"
    assert deps[0]["description"] == ""


def test_extract_dependency_in_array_items() -> None:
    schema = {
        "properties": {
            "items_field": {
                "type": "array",
                "items": {
                    "type": "tool_dependency",
                    "tool_id": "tid-3",
                    "tool_name": "list_tool",
                    "tool_type": "func",
                },
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert len(deps) == 1
    assert deps[0]["property_name"] == "[]"


def test_extract_dependency_in_additional_properties() -> None:
    schema = {
        "properties": {
            "map_field": {
                "type": "object",
                "additionalProperties": {
                    "type": "tool_dependency",
                    "tool_id": "tid-4",
                    "tool_name": "map_tool",
                    "tool_type": "model",
                },
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert len(deps) == 1
    assert deps[0]["property_name"] == "[*]"


def test_extract_dependency_in_any_of() -> None:
    schema = {
        "properties": {
            "union_field": {
                "anyOf": [
                    {"type": "string"},
                    {
                        "type": "tool_dependency",
                        "tool_id": "tid-5",
                        "tool_name": "union_tool",
                        "tool_type": "func",
                    },
                ],
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert len(deps) == 1
    assert deps[0]["tool_name"] == "union_tool"


def test_extract_ignores_non_list_any_of() -> None:
    schema = {
        "properties": {
            "bad_union": {
                "anyOf": "not-a-list",
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert deps == []


def test_extract_dependency_in_one_of() -> None:
    schema = {
        "properties": {
            "union_field": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "tool_dependency",
                        "tool_id": "tid-6",
                        "tool_name": "oneof_tool",
                        "tool_type": "func",
                    },
                ],
            },
        },
    }
    deps = extract_tool_dependencies(schema)
    assert len(deps) == 1
    assert deps[0]["tool_name"] == "oneof_tool"


# MARK: apply_arg_policies_to_schema


def test_apply_arg_policies_non_dict_schema() -> None:
    """Covers line 72: early return for non-dict schema."""
    apply_arg_policies_to_schema("not-a-dict", {})


def test_apply_arg_policies_missing_arg_policies() -> None:
    schema = {"properties": {"x": {"type": "string"}}}
    apply_arg_policies_to_schema(schema, {})
    assert "compose_artifact_policy" not in schema["properties"]["x"]


def test_apply_arg_policies_non_dict_properties() -> None:
    """Covers line 79: properties is not a dict."""
    schema = {"properties": "bad"}
    apply_arg_policies_to_schema(schema, {"arg_policies": {"x": {}}})


def test_apply_arg_policies_skips_non_string_arg_name() -> None:
    """Covers line 83: arg_name is not a string."""
    schema = {"properties": {"x": {"type": "string"}}}
    metadata = {"arg_policies": {123: {"compose_artifact": {"mode": "allow"}}}}
    apply_arg_policies_to_schema(schema, metadata)
    assert "compose_artifact_policy" not in schema["properties"]["x"]


def test_apply_arg_policies_skips_non_dict_policy_map() -> None:
    """Covers line 83: policy_map is not a dict."""
    schema = {"properties": {"x": {"type": "string"}}}
    metadata = {"arg_policies": {"x": "not-a-dict"}}
    apply_arg_policies_to_schema(schema, metadata)
    assert "compose_artifact_policy" not in schema["properties"]["x"]


def test_apply_arg_policies_skips_missing_property() -> None:
    """Covers line 86: property not in schema."""
    schema = {"properties": {"y": {"type": "string"}}}
    metadata = {"arg_policies": {"x": {"compose_artifact": {"mode": "allow"}}}}
    apply_arg_policies_to_schema(schema, metadata)
    assert "compose_artifact_policy" not in schema["properties"]["y"]


def test_apply_arg_policies_skips_non_dict_compose_artifact() -> None:
    """Covers line 98: compose_artifact is not a dict."""
    schema = {"properties": {"x": {"type": "string"}}}
    metadata = {"arg_policies": {"x": {"compose_artifact": "bad"}}}
    apply_arg_policies_to_schema(schema, metadata)
    assert "compose_artifact_policy" not in schema["properties"]["x"]


def test_apply_arg_policies_skips_invalid_mode() -> None:
    """Covers line 103: mode not in allowed set."""
    schema = {"properties": {"x": {"type": "string"}}}
    metadata = {"arg_policies": {"x": {"compose_artifact": {"mode": "invalid"}}}}
    apply_arg_policies_to_schema(schema, metadata)
    assert "compose_artifact_policy" not in schema["properties"]["x"]


def test_apply_arg_policies_normalizes_invalid_approval() -> None:
    """Covers line 105: approval not in allowed set defaults to 'none'."""
    schema = {"properties": {"x": {"type": "string"}}}
    metadata = {
        "arg_policies": {
            "x": {"compose_artifact": {"mode": "allow", "approval": "bogus"}},
        },
    }
    apply_arg_policies_to_schema(schema, metadata)
    policy = schema["properties"]["x"]["compose_artifact_policy"]
    assert policy == {"mode": "allow", "approval": "none"}


def test_apply_arg_policies_appends_to_existing_description() -> None:
    """Covers lines 113-114: appends summary to existing description."""
    schema = {"properties": {"x": {"type": "string", "description": "Original desc."}}}
    metadata = {
        "arg_policies": {
            "x": {"compose_artifact": {"mode": "require", "approval": "explicit"}},
        },
    }
    apply_arg_policies_to_schema(schema, metadata)
    desc = schema["properties"]["x"]["description"]
    assert desc.startswith("Original desc.")
    assert "mode=require" in desc
    assert "approval=explicit" in desc


def test_apply_arg_policies_no_duplicate_summary() -> None:
    """Covers line 113: does not duplicate summary if already present."""
    summary = "Compose artifact policy: mode=allow, approval=none."
    schema = {"properties": {"x": {"type": "string", "description": summary}}}
    metadata = {
        "arg_policies": {
            "x": {"compose_artifact": {"mode": "allow", "approval": "none"}},
        },
    }
    apply_arg_policies_to_schema(schema, metadata)
    assert schema["properties"]["x"]["description"].count(summary) == 1


def test_apply_arg_policies_sets_description_when_empty() -> None:
    """Covers line 116: no existing description."""
    schema = {"properties": {"x": {"type": "string"}}}
    metadata = {
        "arg_policies": {
            "x": {"compose_artifact": {"mode": "forbid"}},
        },
    }
    apply_arg_policies_to_schema(schema, metadata)
    desc = schema["properties"]["x"]["description"]
    assert desc == "Compose artifact policy: mode=forbid, approval=none."


def test_apply_arg_policies_blank_description_treated_as_empty() -> None:
    """Covers line 112: blank string description treated as empty."""
    schema = {"properties": {"x": {"type": "string", "description": "   "}}}
    metadata = {
        "arg_policies": {
            "x": {"compose_artifact": {"mode": "allow"}},
        },
    }
    apply_arg_policies_to_schema(schema, metadata)
    desc = schema["properties"]["x"]["description"]
    assert desc == "Compose artifact policy: mode=allow, approval=none."


# MARK: merge_metadata


def test_merge_metadata_both_none() -> None:
    """Covers line 131-132: both current and incoming are None."""
    result = merge_metadata(None, None)
    assert result == {}


def test_merge_metadata_current_none() -> None:
    result = merge_metadata(None, {"key": "value"})
    assert result == {"key": "value"}


def test_merge_metadata_incoming_none() -> None:
    result = merge_metadata({"key": "value"}, None)
    assert result == {"key": "value"}


def test_merge_metadata_scalar_overwrite() -> None:
    """Covers line 142: scalar from incoming overwrites current."""
    result = merge_metadata({"a": 1}, {"a": 2})
    assert result["a"] == 2


def test_merge_metadata_new_key_added() -> None:
    """Covers lines 136-138: key not in current is added."""
    result = merge_metadata({"a": 1}, {"b": 2})
    assert result == {"a": 1, "b": 2}


def test_merge_metadata_deep_merge_dicts() -> None:
    """Covers lines 133-135: nested dicts are recursively merged."""
    current = {"nested": {"x": 1, "y": 2}}
    incoming = {"nested": {"y": 3, "z": 4}}
    result = merge_metadata(current, incoming)
    assert result["nested"] == {"x": 1, "y": 3, "z": 4}


def test_merge_metadata_list_deduplication() -> None:
    """Covers lines 139-140: lists are concatenated with deduplication."""
    current = {"tags": ["a", "b"]}
    incoming = {"tags": ["b", "c"]}
    result = merge_metadata(current, incoming)
    assert result["tags"] == ["a", "b", "c"]


def test_merge_metadata_list_preserves_order() -> None:
    current = {"items": [3, 1, 2]}
    incoming = {"items": [2, 4]}
    result = merge_metadata(current, incoming)
    assert result["items"] == [3, 1, 2, 4]


def test_merge_metadata_does_not_mutate_current() -> None:
    current = {"a": {"b": 1}}
    incoming = {"a": {"c": 2}}
    merge_metadata(current, incoming)
    assert current == {"a": {"b": 1}}
