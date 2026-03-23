"""Schema type processors for JSON schema transformations.

Handles processing of $ref, array, tuple, object, and anyOf schema types,
converting nested Pydantic model references into tool dependency schemas.
"""

from __future__ import annotations

from typing import Any

from .dependency_detector import DependencyDetector

# MARK: Schema Type Processor


class SchemaTypeProcessor:
    """Processes JSON schema types and converts model references to tool dependencies.

    Handles $ref resolution, array/tuple/object schema processing,
    and anyOf variant extraction for nested model dependencies.
    """

    def __init__(
        self,
        dependency_detector: DependencyDetector,
        resolve_tool_id: Any,
    ) -> None:
        """Initialize the schema type processor.

        Args:
            dependency_detector: Detector for building dependency schemas.
            resolve_tool_id: Callable that resolves a model name to a tool ID.
        """
        self._dependency_detector = dependency_detector
        self._resolve_tool_id = resolve_tool_id

    # MARK: - Dispatch

    def process_schema_by_type(self, prop_schema: dict[str, Any]) -> dict[str, Any]:
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

    def _process_ref(self, prop_schema: dict[str, Any]) -> dict[str, Any]:
        """Process a $ref property (nested model reference)."""
        ref_path = prop_schema["$ref"]
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

    def _process_array(self, prop_schema: dict[str, Any]) -> dict[str, Any]:
        """Process an array property that may contain model items."""
        if "prefixItems" in prop_schema:
            return self._process_tuple(prop_schema)

        if "items" not in prop_schema:
            return prop_schema

        items_schema = prop_schema.get("items")
        if not isinstance(items_schema, dict):
            return prop_schema

        result = prop_schema.copy()
        result["items"] = self.process_schema_by_type(items_schema)
        return result

    def _process_tuple(self, prop_schema: dict[str, Any]) -> dict[str, Any]:
        """Process a tuple property with prefixItems."""
        prefix_items = prop_schema.get("prefixItems", [])
        processed_items = []

        for item_schema in prefix_items:
            if not isinstance(item_schema, dict):
                processed_items.append(item_schema)
                continue
            processed_items.append(self.process_schema_by_type(item_schema))

        result = prop_schema.copy()
        result["prefixItems"] = processed_items
        return result

    # MARK: - Object Processing

    def _process_object(self, prop_schema: dict[str, Any]) -> dict[str, Any]:
        """Process an object property with additionalProperties."""
        additional_schema = prop_schema["additionalProperties"]

        if not isinstance(additional_schema, dict):
            return prop_schema

        result = prop_schema.copy()
        result["additionalProperties"] = self.process_schema_by_type(additional_schema)
        return result

    # MARK: - Union Processing

    def _process_union(self, prop_schema: dict[str, Any], union_key: str) -> dict[str, Any]:
        """Process anyOf/oneOf variants recursively, preserving non-model branches."""
        result = prop_schema.copy()
        result[union_key] = [
            self.process_schema_by_type(variant) if isinstance(variant, dict) else variant
            for variant in prop_schema.get(union_key, [])
        ]
        return result


__all__ = ["SchemaTypeProcessor"]
