"""Unified schema builder for tool specifications.

Generates JSON schemas for both function tools and Pydantic model tools,
including dependency field handling and nested model flattening.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from maivn_shared import ArgsSchema, create_uuid
from pydantic import BaseModel

from .dependency_detector import DependencyDetector
from .model_discovery import find_model_class
from .schema_processors import SchemaTypeProcessor
from .type_utils import is_pydantic_model

# MARK: Schema Builder


class SchemaBuilder:
    """Creates schemas for functions and Pydantic models with tool dependencies.

    This unified builder handles both function signature schemas and model
    schemas, converting nested Pydantic models into tool dependency references.
    """

    def __init__(self) -> None:
        """Initialize the schema builder."""
        self._model_classes: dict[str, type[BaseModel]] = {}
        self._processed_models: dict[type[BaseModel], str] = {}
        self._available_function_tools: list[Callable[..., Any]] = []
        self._dependency_detector = DependencyDetector()
        self._schema_processor = SchemaTypeProcessor(
            dependency_detector=self._dependency_detector,
            resolve_tool_id=self._resolve_tool_id,
        )

    # MARK: - Public API

    def set_function_tools(self, function_tools: list[Callable[..., Any]]) -> None:
        """Set the available function tools for dependency detection."""
        self._available_function_tools = function_tools

    def get_tool_id_for_model(self, model: type[BaseModel]) -> str:
        """Get deterministic tool ID for a model class."""
        if model in self._processed_models:
            return self._processed_models[model]
        return create_uuid(model)

    def create_from_function(self, func: Callable[..., Any], tool_id: str) -> ArgsSchema:
        """Create schema from function signature.

        Args:
            func: The function to create schema for
            tool_id: Deterministic UUID for the tool

        Returns:
            Schema dictionary with properties and required fields
        """
        signature = inspect.signature(func)
        properties, required = self._process_function_parameters(signature, func)
        return_schema = self._extract_return_type(signature)

        return {
            "tool_id": tool_id,
            "tool_type": "func",
            "description": func.__doc__ or "",
            "properties": properties,
            "required": required,
            "return_type": return_schema,
        }

    def create_from_model(self, model: type[BaseModel], tool_id: str) -> ArgsSchema:
        """Create schema from Pydantic model with flattened dependencies.

        Args:
            model: The Pydantic model to create schema for
            tool_id: Deterministic UUID for the tool

        Returns:
            Schema dictionary with explicit tool dependencies
        """
        self._register_model(model, tool_id)
        model_schema = model.model_json_schema()
        self._register_nested_models(model_schema.get("$defs", {}), model.__module__)

        all_properties = self._process_model_properties(model_schema, model)
        properties, data_dep_fields = self._separate_data_dependencies(all_properties)
        required = [f for f in model_schema.get("required", []) if f not in data_dep_fields]

        return {
            "tool_id": tool_id,
            "tool_type": "model",
            "description": model.__doc__ or "",
            "properties": properties,
            "required": required,
        }

    # MARK: - Function Schema Building

    def _process_function_parameters(
        self,
        signature: inspect.Signature,
        func: Callable[..., Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Process all function parameters into schema properties."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in signature.parameters.items():
            if param_name == "self":
                continue

            param_schema = self._build_parameter_schema(param, func)
            properties[param_name] = param_schema["schema"]

            if param_schema["required"]:
                required.append(param_name)

        return properties, required

    def _build_parameter_schema(
        self,
        param: inspect.Parameter,
        func: Callable[..., Any],
    ) -> dict[str, Any]:
        """Build schema for a function parameter."""
        if param.annotation == inspect.Parameter.empty:
            raise ValueError(f"Parameter '{param.name}' missing type annotation")

        is_required = param.default == inspect.Parameter.empty

        dep_schema = self._try_func_dependency_schema(param.name, func)
        if dep_schema:
            return {"schema": dep_schema, "required": is_required}

        if is_pydantic_model(param.annotation):
            return {
                "schema": self._build_model_dependency(param.annotation),
                "required": is_required,
            }

        return {
            "schema": self._build_primitive_schema(param.annotation),
            "required": is_required,
        }

    def _try_func_dependency_schema(
        self,
        param_name: str,
        func: Callable[..., Any],
    ) -> dict[str, Any] | None:
        """Try to detect dependency from function decorator."""
        dependencies = getattr(func, "_dependencies", [])
        return self._dependency_detector.detect_dependency(
            dependencies=dependencies,
            arg_name=param_name,
            context_name=func.__name__,
        )

    def _extract_return_type(self, signature: inspect.Signature) -> dict[str, Any]:
        """Extract return type schema from function signature."""
        if signature.return_annotation == inspect.Signature.empty:
            return {}

        return_annotation = signature.return_annotation

        if is_pydantic_model(return_annotation):
            return self._build_model_dependency(return_annotation)

        return self._build_primitive_schema(return_annotation, is_return=True)

    # MARK: - Model Schema Building

    def _register_model(self, model: type[BaseModel], tool_id: str) -> None:
        """Register a model for dependency resolution."""
        self._processed_models[model] = tool_id
        self._model_classes[model.__name__] = model

    def _register_nested_models(self, defs: dict[str, Any], context_module: str) -> None:
        """Discover and register model classes from $defs."""
        for def_name in defs:
            if def_name not in self._model_classes:
                model_class = find_model_class(def_name, context_module)
                if model_class:
                    self._model_classes[def_name] = model_class

    def _process_model_properties(
        self,
        model_schema: dict[str, Any],
        model: type[BaseModel],
    ) -> dict[str, Any]:
        """Process all model properties and convert nested models to dependencies."""
        return {
            prop_name: self._process_property(prop_schema, prop_name, model)
            for prop_name, prop_schema in model_schema.get("properties", {}).items()
        }

    def _process_property(
        self,
        prop_schema: dict[str, Any],
        prop_name: str,
        model: type[BaseModel],
    ) -> dict[str, Any]:
        """Process a model property, converting nested models to tool dependencies."""
        if dep_schema := self._try_model_dependency_schema(prop_name, model):
            return dep_schema

        if dep_schema := self._try_function_tool_dependency(prop_schema, prop_name):
            return dep_schema

        return self._schema_processor.process_schema_by_type(prop_schema)

    def _try_model_dependency_schema(
        self,
        prop_name: str,
        model: type[BaseModel],
    ) -> dict[str, Any] | None:
        """Try to detect dependency from model decorator."""
        dependencies = getattr(model, "_dependencies", [])
        return self._dependency_detector.detect_dependency(
            dependencies=dependencies,
            arg_name=prop_name,
            context_name=model.__name__,
        )

    # MARK: - Function Tool Dependencies

    def _try_function_tool_dependency(
        self,
        prop_schema: dict[str, Any],
        prop_name: str,
    ) -> dict[str, Any] | None:
        """Try to create function tool dependency if applicable."""
        if not self._is_function_tool_candidate(prop_schema):
            return None

        function_tool = self._find_matching_function_tool(prop_name)
        if not function_tool:
            return None

        tool_id = create_uuid(function_tool)
        tool_name = getattr(function_tool, "__name__", "unknown_function")

        return {
            "type": "tool_dependency",
            "tool_id": tool_id,
            "tool_name": tool_name,
            "tool_type": "func",
            "description": f"Output from {tool_name}",
            "output_type": "object",
        }

    def _is_function_tool_candidate(self, prop_schema: dict[str, Any]) -> bool:
        """Check if a property should be a function tool dependency."""
        if prop_schema.get("type") != "object":
            return False
        if prop_schema.get("additionalProperties") is not True:
            return False

        description = prop_schema.get("description", "").lower()
        keywords = ["calculation", "calculated", "computed", "results", "output", "specs"]

        return any(keyword in description for keyword in keywords)

    def _find_matching_function_tool(self, prop_name: str) -> Callable[..., Any] | None:
        """Find a function tool that matches this property."""
        prop_normalized = prop_name.replace("_specs", "").replace("_data", "")

        for tool in self._available_function_tools:
            if not hasattr(tool, "__name__"):
                continue

            tool_name = tool.__name__
            tool_normalized = (
                tool_name.replace("calculate_", "")
                .replace("_displacement", "")
                .replace("_capacity", "")
                .replace("_geometry", "")
                .replace("_coefficient", "")
            )

            if prop_normalized in tool_name.lower() or tool_normalized in prop_name.lower():
                return tool

        return None

    # MARK: - Data Dependency Separation

    def _separate_data_dependencies(
        self,
        all_properties: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Separate data_dependency fields from regular properties."""
        properties: dict[str, Any] = {}
        data_dep_fields: list[str] = []

        for prop_name, prop_schema in all_properties.items():
            if isinstance(prop_schema, dict) and prop_schema.get("type") == "data_dependency":
                data_dep_fields.append(prop_name)
            properties[prop_name] = prop_schema

        return properties, data_dep_fields

    # MARK: - Primitive Schema Building

    def _build_model_dependency(self, model: type[BaseModel]) -> dict[str, Any]:
        """Build tool dependency schema for a Pydantic model."""
        tool_id = self.get_tool_id_for_model(model)
        return self._dependency_detector.build_model_tool_dependency(
            tool_id=tool_id,
            model_name=model.__name__,
        )

    def _build_primitive_schema(
        self,
        annotation: Any,
        is_return: bool = False,
    ) -> dict[str, Any]:
        """Build schema for primitive/non-model types."""
        try:
            from pydantic import TypeAdapter

            adapter = TypeAdapter(annotation)
            schema = adapter.json_schema()
            schema.pop("$defs", None)
            return schema
        except Exception as e:
            if is_return:
                return {
                    "type": "object",
                    "description": "Complex return type",
                    "note": f"Schema generation failed: {e!s}",
                }
            return {
                "type": "string",
                "description": f"Complex type: {annotation}",
                "note": f"Schema generation failed: {e!s}",
            }

    def _resolve_tool_id(self, model_name: str) -> str:
        """Resolve tool ID for a model name."""
        model_class = self._model_classes.get(model_name)
        if model_class:
            return self.get_tool_id_for_model(model_class)
        return f"placeholder-{model_name.lower()}-tool-id"


__all__ = ["SchemaBuilder"]
