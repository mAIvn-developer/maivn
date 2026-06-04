"""Schema type processors for JSON schema transformations.

Handles processing of $ref, array, tuple, object, and anyOf schema types,
converting nested Pydantic model references into tool dependency schemas.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias, cast

from pydantic import JsonValue

from .dependency_detector import DependencyDetector

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]

# MARK: Schema Type Processor


class SchemaTypeProcessor:
    """Processes JSON schema types and converts model references to tool dependencies.

    Handles $ref resolution, array/tuple/object schema processing,
    and anyOf variant extraction for nested model dependencies.
    """

    def __init__(
        self,
        dependency_detector: DependencyDetector,
        resolve_tool_id: Callable[[str], str],
    ) -> None:
        """Initialize the schema type processor.

        Args:
            dependency_detector: Detector for building dependency schemas.
            resolve_tool_id: Callable that resolves a model name to a tool ID.
        """
        self._dependency_detector: DependencyDetector = dependency_detector
        self._resolve_tool_id: Callable[[str], str] = resolve_tool_id

    # MARK: - Dispatch

    def process_schema_by_type(self, prop_schema: JsonObject) -> JsonObject:
        """Process schema based on its type."""
        if "$ref" in prop_schema:
            return self._process_ref(prop_schema)

        for union_key in ("anyOf", "oneOf"):
            if union_key in prop_schema and isinstance(prop_schema[union_key], list):
                return self._process_union(prop_schema, union_key)

        schema_type = prop_schema.get("type")

        if schema_type == "array":
            return self._process_array(prop_schema)

        if schema_type == "object" and "additionalProperties" in prop_schema:
            return self._process_object(prop_schema)

        return prop_schema

    # MARK: - Ref Processing

    def _process_ref(self, prop_schema: JsonObject) -> JsonObject:
        """Process a $ref property (nested model reference)."""
        ref_path = cast(str, prop_schema["$ref"])
        if not ref_path.startswith("#/$defs/"):
            return prop_schema

        model_name = ref_path.split("/")[-1]
        tool_id = self._resolve_tool_id(model_name)

        return self._dependency_detector.build_model_tool_dependency(
            tool_id=tool_id,
            model_name=model_name,
            ref_path=ref_path,
        )

    # MARK: - Array Processing

    def _process_array(self, prop_schema: JsonObject) -> JsonObject:
        """Process an array property that may contain model items."""
        if "prefixItems" in prop_schema:
            return self._process_tuple(prop_schema)

        if "items" not in prop_schema:
            return prop_schema

        items_schema = prop_schema.get("items")
        if not isinstance(items_schema, dict):
            return prop_schema

        result = prop_schema.copy()
        result["items"] = self.process_schema_by_type(cast(JsonObject, items_schema))
        return result

    def _process_tuple(self, prop_schema: JsonObject) -> JsonObject:
        """Process a tuple property with prefixItems."""
        prefix_items = cast(list[JsonValue], prop_schema.get("prefixItems", []))
        processed_items: list[JsonValue] = []

        for item_schema in prefix_items:
            if not isinstance(item_schema, dict):
                processed_items.append(item_schema)
                continue
            processed_items.append(self.process_schema_by_type(cast(JsonObject, item_schema)))

        result = prop_schema.copy()
        result["prefixItems"] = processed_items
        return result

    # MARK: - Object Processing

    def _process_object(self, prop_schema: JsonObject) -> JsonObject:
        """Process an object property with additionalProperties."""
        additional_schema = prop_schema["additionalProperties"]

        if not isinstance(additional_schema, dict):
            return prop_schema

        result = prop_schema.copy()
        result["additionalProperties"] = self.process_schema_by_type(
            cast(JsonObject, additional_schema)
        )
        return result

    # MARK: - Union Processing

    def _process_union(self, prop_schema: JsonObject, union_key: str) -> JsonObject:
        """Process anyOf/oneOf variants recursively, preserving non-model branches."""
        result = prop_schema.copy()
        variants = cast(list[JsonValue], prop_schema.get(union_key, []))
        result[union_key] = [
            self.process_schema_by_type(cast(JsonObject, variant))
            if isinstance(variant, dict)
            else variant
            for variant in variants
        ]
        return result


__all__ = ["SchemaTypeProcessor"]
