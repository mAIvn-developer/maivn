from __future__ import annotations

from maivn._internal.core.tool_specs.dependency_detector import DependencyDetector
from maivn._internal.core.tool_specs.schema_processors import SchemaTypeProcessor

# MARK: Fixtures


def _make_processor(
    resolve_tool_id: object | None = None,
) -> SchemaTypeProcessor:
    """Create a SchemaTypeProcessor with a default resolve_tool_id."""

    def _default_resolve(name: str) -> str:
        return f"tool-{name}"

    if resolve_tool_id is None:
        resolve_tool_id = _default_resolve
    return SchemaTypeProcessor(
        dependency_detector=DependencyDetector(),
        resolve_tool_id=resolve_tool_id,
    )


# MARK: Dispatch


class TestProcessSchemaByType:
    """Tests for the top-level dispatch method."""

    def test_passthrough_for_plain_schema(self) -> None:
        processor = _make_processor()
        schema = {"type": "string", "description": "A name"}
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_dispatches_to_ref(self) -> None:
        processor = _make_processor()
        schema = {"$ref": "#/$defs/MyModel"}
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "tool_dependency"
        assert result["tool_name"] == "MyModel"

    def test_dispatches_to_array(self) -> None:
        processor = _make_processor()
        schema = {"type": "array", "items": {"type": "string"}}
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_dispatches_to_object_with_additional_properties(self) -> None:
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": {"$ref": "#/$defs/Item"},
        }
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "object"
        assert result["additionalProperties"]["tool_name"] == "Item"

    def test_object_without_additional_properties_passes_through(self) -> None:
        processor = _make_processor()
        schema = {"type": "object", "properties": {"x": {"type": "int"}}}
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_dispatches_to_top_level_anyof(self) -> None:
        processor = _make_processor()
        schema = {
            "anyOf": [
                {"$ref": "#/$defs/Alpha"},
                {"type": "null"},
            ]
        }
        result = processor.process_schema_by_type(schema)
        assert result["anyOf"][0]["type"] == "tool_dependency"
        assert result["anyOf"][0]["tool_name"] == "Alpha"
        assert result["anyOf"][1] == {"type": "null"}


# MARK: Ref Processing


class TestProcessRef:
    """Tests for $ref processing (lines 56-69)."""

    def test_ref_with_defs_prefix_resolves_to_dependency(self) -> None:
        processor = _make_processor()
        schema = {"$ref": "#/$defs/Address"}
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "tool_dependency"
        assert result["tool_id"] == "tool-Address"
        assert result["tool_name"] == "Address"
        assert result["original_ref"] == "#/$defs/Address"

    def test_ref_without_defs_prefix_passes_through(self) -> None:
        """Line 60: non-#/$defs/ ref is returned as-is."""
        processor = _make_processor()
        schema = {"$ref": "https://example.com/schema.json"}
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_ref_with_external_path_passes_through(self) -> None:
        processor = _make_processor()
        schema = {"$ref": "#/definitions/Legacy"}
        result = processor.process_schema_by_type(schema)
        assert result == schema


# MARK: Array Processing


class TestProcessArray:
    """Tests for array processing (lines 73-99)."""

    def test_array_with_ref_items_resolves_dependency(self) -> None:
        processor = _make_processor()
        schema = {"type": "array", "items": {"$ref": "#/$defs/Task"}}
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "array"
        assert result["items"]["type"] == "tool_dependency"
        assert result["items"]["tool_name"] == "Task"

    def test_array_with_non_defs_ref_items_passes_through(self) -> None:
        processor = _make_processor()
        schema = {"type": "array", "items": {"$ref": "#/other/Place"}}
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_array_with_anyof_items_resolves_dependencies(self) -> None:
        """Lines 94-98: array items with anyOf containing $ref variants."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"$ref": "#/$defs/Cat"},
                    {"$ref": "#/$defs/Dog"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "array"
        assert "anyOf" in result["items"]
        deps = result["items"]["anyOf"]
        assert len(deps) == 2
        assert deps[0]["tool_name"] == "Cat"
        assert deps[1]["tool_name"] == "Dog"

    def test_array_with_empty_anyof_passes_through(self) -> None:
        """Lines 96-98: anyOf with no $ref variants returns original schema."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "integer"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_array_with_no_items_passes_through(self) -> None:
        processor = _make_processor()
        schema = {"type": "array"}
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_array_with_prefix_items_dispatches_to_tuple(self) -> None:
        """Line 76: prefixItems triggers tuple processing."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "prefixItems": [
                {"$ref": "#/$defs/First"},
                {"type": "string"},
            ],
        }
        result = processor.process_schema_by_type(schema)
        assert result["prefixItems"][0]["type"] == "tool_dependency"
        assert result["prefixItems"][0]["tool_name"] == "First"
        assert result["prefixItems"][1] == {"type": "string"}


# MARK: Tuple Processing


class TestProcessTuple:
    """Tests for tuple/prefixItems processing (lines 101-127)."""

    def test_tuple_with_all_refs(self) -> None:
        """Lines 103-127: all prefixItems are $ref."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "prefixItems": [
                {"$ref": "#/$defs/Alpha"},
                {"$ref": "#/$defs/Beta"},
            ],
        }
        result = processor.process_schema_by_type(schema)
        assert len(result["prefixItems"]) == 2
        assert result["prefixItems"][0]["tool_name"] == "Alpha"
        assert result["prefixItems"][1]["tool_name"] == "Beta"

    def test_tuple_with_mixed_items(self) -> None:
        """Lines 107-108: non-ref items are kept as-is."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "prefixItems": [
                {"type": "integer"},
                {"$ref": "#/$defs/Config"},
                "not_a_dict",
            ],
        }
        result = processor.process_schema_by_type(schema)
        assert result["prefixItems"][0] == {"type": "integer"}
        assert result["prefixItems"][1]["tool_name"] == "Config"
        assert result["prefixItems"][2] == "not_a_dict"

    def test_tuple_with_non_defs_ref(self) -> None:
        """Lines 112-113: $ref not starting with #/$defs/ is kept as-is."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "prefixItems": [
                {"$ref": "#/definitions/Legacy"},
            ],
        }
        result = processor.process_schema_by_type(schema)
        assert result["prefixItems"][0] == {"$ref": "#/definitions/Legacy"}

    def test_tuple_preserves_other_keys(self) -> None:
        """Line 125: result is a copy with other keys preserved."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "minItems": 1,
            "maxItems": 3,
        }
        result = processor.process_schema_by_type(schema)
        assert result["minItems"] == 1
        assert result["maxItems"] == 3

    def test_tuple_with_empty_prefix_items(self) -> None:
        processor = _make_processor()
        schema = {"type": "array", "prefixItems": []}
        result = processor.process_schema_by_type(schema)
        assert result["prefixItems"] == []


# MARK: Object Processing


class TestProcessObject:
    """Tests for object processing (lines 131-159)."""

    def test_object_with_ref_additional_properties(self) -> None:
        """Lines 133-152: additionalProperties with $ref."""
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": {"$ref": "#/$defs/Value"},
        }
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "object"
        assert result["additionalProperties"]["type"] == "tool_dependency"
        assert result["additionalProperties"]["tool_name"] == "Value"

    def test_object_with_non_defs_ref_additional_properties(self) -> None:
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": {"$ref": "#/other/Thing"},
        }
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_object_with_non_dict_additional_properties(self) -> None:
        """Line 135-136: additionalProperties is not a dict (e.g., True)."""
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": True,
        }
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_object_with_anyof_additional_properties(self) -> None:
        """Lines 154-157: additionalProperties with anyOf."""
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": {
                "anyOf": [
                    {"$ref": "#/$defs/Foo"},
                    {"$ref": "#/$defs/Bar"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        assert result["type"] == "object"
        deps = result["additionalProperties"]["anyOf"]
        assert len(deps) == 2
        assert deps[0]["tool_name"] == "Foo"
        assert deps[1]["tool_name"] == "Bar"

    def test_object_with_empty_anyof_additional_properties_passes_through(self) -> None:
        """Lines 156-157: anyOf with no refs returns original."""
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": {
                "anyOf": [
                    {"type": "string"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        assert result == schema

    def test_object_with_plain_dict_additional_properties_passes_through(self) -> None:
        """Line 159: additionalProperties is a dict but no $ref or anyOf."""
        processor = _make_processor()
        schema = {
            "type": "object",
            "additionalProperties": {"type": "string"},
        }
        result = processor.process_schema_by_type(schema)
        assert result == schema


# MARK: AnyOf Processing


class TestProcessAnyOf:
    """Tests for anyOf variant processing (lines 163-185)."""

    def test_anyof_extracts_all_ref_variants(self) -> None:
        """Lines 165-183: multiple $ref variants are resolved."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"$ref": "#/$defs/A"},
                    {"$ref": "#/$defs/B"},
                    {"$ref": "#/$defs/C"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        deps = result["items"]["anyOf"]
        assert len(deps) == 3
        assert [d["tool_name"] for d in deps] == ["A", "B", "C"]

    def test_anyof_preserves_non_dict_variants(self) -> None:
        """Non-dict variants are preserved while refs are resolved."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    "not_a_dict",
                    {"$ref": "#/$defs/Valid"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        variants = result["items"]["anyOf"]
        assert variants[0] == "not_a_dict"
        assert variants[1]["tool_name"] == "Valid"

    def test_anyof_preserves_non_ref_dicts(self) -> None:
        """Non-ref dict variants are preserved while refs are resolved."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"$ref": "#/$defs/Model"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        variants = result["items"]["anyOf"]
        assert variants[0] == {"type": "string"}
        assert variants[1]["tool_name"] == "Model"

    def test_anyof_preserves_non_defs_refs(self) -> None:
        """Non-#/$defs refs are preserved while model refs are resolved."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"$ref": "#/definitions/Old"},
                    {"$ref": "#/$defs/New"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        variants = result["items"]["anyOf"]
        assert variants[0] == {"$ref": "#/definitions/Old"}
        assert variants[1]["tool_name"] == "New"

    def test_anyof_returns_empty_when_no_valid_refs(self) -> None:
        """Lines 165, 185: returns empty list when no valid refs found."""
        processor = _make_processor()
        schema = {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                ],
            },
        }
        result = processor.process_schema_by_type(schema)
        # Empty tool_deps means original schema is returned
        assert result == schema


# MARK: Resolve Tool ID


class TestResolveToolId:
    """Tests verifying the resolve_tool_id callable is used correctly."""

    def test_custom_resolve_tool_id_is_called(self) -> None:
        calls: list[str] = []

        def custom_resolve(name: str) -> str:
            calls.append(name)
            return f"custom-{name}"

        processor = _make_processor(resolve_tool_id=custom_resolve)
        schema = {"$ref": "#/$defs/Widget"}
        result = processor.process_schema_by_type(schema)

        assert calls == ["Widget"]
        assert result["tool_id"] == "custom-Widget"
