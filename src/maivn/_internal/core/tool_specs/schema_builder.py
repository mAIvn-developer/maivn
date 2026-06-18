"""Unified schema builder for tool specifications.

Generates JSON schemas for both function tools and Pydantic model tools,
including dependency field handling and nested model flattening.
"""

# pyright: strict
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated, Final, TypeAlias, cast, get_args, get_origin, get_type_hints

from maivn_shared import ArgsSchema, BaseDependency, create_uuid
from pydantic import BaseModel, JsonValue, TypeAdapter

from .dependency_detector import DependencyDetector
from .model_discovery import find_model_class
from .schema_processors import SchemaTypeProcessor
from .type_utils import is_pydantic_model

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
FunctionToolCallable: TypeAlias = Callable[..., object]


# MARK: Function Tool Matching

_FUNCTION_TOOL_CANDIDATE_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "calculation",
        "calculated",
        "computed",
        "results",
        "output",
        "specs",
    }
)
_FUNCTION_TOOL_PROPERTY_TOKENS: Final[tuple[str, ...]] = ("_specs", "_data")
_FUNCTION_TOOL_NAME_TOKENS: Final[tuple[str, ...]] = (
    "calculate_",
    "_displacement",
    "_capacity",
    "_geometry",
    "_coefficient",
)

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
        self._available_function_tools: list[FunctionToolCallable] = []
        self._dependency_detector: DependencyDetector = DependencyDetector()
        self._schema_processor: SchemaTypeProcessor = SchemaTypeProcessor(
            dependency_detector=self._dependency_detector,
            resolve_tool_id=self._resolve_tool_id,
        )

    # MARK: - Public API

    def set_function_tools(self, function_tools: list[FunctionToolCallable]) -> None:
        """Set the available function tools for dependency detection."""
        self._available_function_tools = function_tools

    def get_tool_id_for_model(self, model: type[BaseModel]) -> str:
        """Get deterministic tool ID for a model class."""
        if model in self._processed_models:
            return self._processed_models[model]
        return create_uuid(model)

    def create_from_function(self, func: FunctionToolCallable, tool_id: str) -> ArgsSchema:
        """Create schema from function signature.

        Args:
            func: The function to create schema for
            tool_id: Deterministic UUID for the tool

        Returns:
            Schema dictionary with properties and required fields
        """
        signature = inspect.signature(func)
        properties, required = self._process_function_parameters(signature, func)
        return_schema = self._extract_return_type(signature, func)

        schema: JsonObject = {
            "tool_id": tool_id,
            "tool_type": "func",
            "description": _get_doc(func),
            "properties": cast(JsonValue, properties),
            "required": cast(JsonValue, required),
            "return_type": cast(JsonValue, return_schema),
        }
        return schema

    def create_from_model(
        self,
        model: type[BaseModel],
        tool_id: str,
        *,
        inline_model_refs: bool = False,
    ) -> ArgsSchema:
        """Create schema from Pydantic model with flattened dependencies.

        Args:
            model: The Pydantic model to create schema for
            tool_id: Deterministic UUID for the tool

        Returns:
            Schema dictionary with explicit tool dependencies
        """
        self._register_model(model, tool_id)
        model_schema = cast(JsonObject, model.model_json_schema())
        self._register_nested_models(_get_object_value(model_schema, "$defs"), model.__module__)

        all_properties = self._process_model_properties(
            model_schema,
            model,
            inline_model_refs=inline_model_refs,
        )
        properties, data_dep_fields = self._separate_data_dependencies(all_properties)
        required = [
            field_name
            for field_name in _get_str_list_value(model_schema, "required")
            if field_name not in data_dep_fields
        ]

        schema: JsonObject = {
            "tool_id": tool_id,
            "tool_type": "model",
            "description": _get_doc(model),
            "properties": cast(JsonValue, properties),
            "required": cast(JsonValue, required),
        }
        if inline_model_refs or _contains_local_ref(properties):
            processed_defs = self._process_model_definitions(
                _get_object_value(model_schema, "$defs")
            )
            if processed_defs:
                schema["$defs"] = cast(JsonValue, processed_defs)
        return schema

    # MARK: - Function Schema Building

    def _process_function_parameters(
        self,
        signature: inspect.Signature,
        func: FunctionToolCallable,
    ) -> tuple[JsonObject, list[str]]:
        """Process all function parameters into schema properties."""
        properties: JsonObject = {}
        required: list[str] = []
        resolved_hints = self._resolve_type_hints(func)

        for param_name, param in signature.parameters.items():
            if param_name == "self":
                continue

            property_schema, is_required = self._build_parameter_schema(
                param,
                func,
                resolved_annotation=resolved_hints.get(param_name),
            )
            properties[param_name] = cast(JsonValue, property_schema)

            if is_required:
                required.append(param_name)

        return properties, required

    @staticmethod
    def _resolve_type_hints(func: FunctionToolCallable) -> dict[str, object]:
        """Resolve string annotations (``from __future__ import annotations``)
        into actual type objects.

        ``include_extras=True`` keeps :pep:`593` ``Annotated[...]`` metadata
        intact; without it Pydantic ``Field`` descriptions and bare-string
        parameter descriptions would be stripped before they ever reached
        the JSON schema.
        """
        try:
            return cast(dict[str, object], get_type_hints(func, include_extras=True))
        except Exception:  # noqa: BLE001 - annotation evaluation may execute third-party refs.
            return {}

    def _build_parameter_schema(
        self,
        param: inspect.Parameter,
        func: FunctionToolCallable,
        resolved_annotation: object | None = None,
    ) -> tuple[JsonObject, bool]:
        """Build schema for a function parameter."""
        raw_annotation = cast(object, param.annotation)
        if raw_annotation == inspect.Parameter.empty:
            raise ValueError(f"Parameter '{param.name}' missing type annotation")

        # Prefer the resolved annotation (which preserves Annotated metadata)
        # over the raw inspect annotation (which may be a string under
        # ``from __future__ import annotations``).
        annotation = resolved_annotation if resolved_annotation is not None else raw_annotation

        raw_default = cast(object, param.default)
        is_required = raw_default == inspect.Parameter.empty

        dep_schema = self._try_func_dependency_schema(param.name, func)
        if dep_schema:
            return dep_schema, is_required

        if is_pydantic_model(annotation):
            return self._build_model_dependency(annotation), is_required

        return self._build_primitive_schema(annotation), is_required

    def _try_func_dependency_schema(
        self,
        param_name: str,
        func: FunctionToolCallable,
    ) -> JsonObject | None:
        """Try to detect dependency from function decorator."""
        dependencies = _get_attached_dependencies(func)
        return self._dependency_detector.detect_dependency(
            dependencies=dependencies,
            arg_name=param_name,
            context_name=_require_callable_name(func),
        )

    def _extract_return_type(
        self,
        signature: inspect.Signature,
        func: FunctionToolCallable | None = None,
    ) -> JsonObject:
        """Extract return type schema from function signature."""
        raw_return_annotation = cast(object, signature.return_annotation)
        if raw_return_annotation == inspect.Signature.empty:
            return {}

        return_annotation = raw_return_annotation
        if func is not None:
            resolved = self._resolve_type_hints(func).get("return")
            if resolved is not None:
                return_annotation = resolved

        if is_pydantic_model(return_annotation):
            return self._build_model_dependency(return_annotation)

        return self._build_primitive_schema(return_annotation, is_return=True)

    # MARK: - Model Schema Building

    def _register_model(self, model: type[BaseModel], tool_id: str) -> None:
        """Register a model for dependency resolution."""
        self._processed_models[model] = tool_id
        self._model_classes[model.__name__] = model

    def _register_nested_models(self, defs: JsonObject, context_module: str) -> None:
        """Discover and register model classes from $defs."""
        for def_name in defs:
            if def_name not in self._model_classes:
                model_class = find_model_class(def_name, context_module)
                if model_class:
                    self._model_classes[def_name] = model_class

    def _process_model_properties(
        self,
        model_schema: JsonObject,
        model: type[BaseModel],
        *,
        inline_model_refs: bool = False,
    ) -> JsonObject:
        """Process all model properties and convert nested models to dependencies."""
        processed: JsonObject = {}
        for prop_name, prop_schema in _get_object_value(model_schema, "properties").items():
            if isinstance(prop_schema, dict):
                processed[prop_name] = cast(
                    JsonValue,
                    self._process_property(
                        cast(JsonObject, prop_schema),
                        prop_name,
                        model,
                        inline_model_refs=inline_model_refs,
                    ),
                )
        return processed

    def _process_model_definitions(self, defs: JsonObject) -> JsonObject:
        """Process nested model definitions while keeping them inline."""
        processed_defs: JsonObject = {}

        for def_name, def_schema in defs.items():
            if not isinstance(def_schema, dict):
                processed_defs[def_name] = def_schema
                continue

            def_schema_obj = cast(JsonObject, def_schema)
            model_class = self._model_classes.get(def_name)
            if model_class is None:
                processed_defs[def_name] = cast(JsonValue, def_schema_obj)
                continue

            all_properties = self._process_model_properties(
                def_schema_obj,
                model_class,
                inline_model_refs=True,
            )
            properties, data_dep_fields = self._separate_data_dependencies(all_properties)
            required = [
                field_name
                for field_name in _get_str_list_value(def_schema_obj, "required")
                if field_name not in data_dep_fields
            ]

            processed_def = dict(def_schema_obj)
            processed_def["properties"] = cast(JsonValue, properties)
            if required:
                processed_def["required"] = cast(JsonValue, required)
            else:
                processed_def.pop("required", None)
            processed_defs[def_name] = cast(JsonValue, processed_def)

        return processed_defs

    def _process_property(
        self,
        prop_schema: JsonObject,
        prop_name: str,
        model: type[BaseModel],
        *,
        inline_model_refs: bool = False,
    ) -> JsonObject:
        """Process a model property, converting nested models to tool dependencies."""
        if dep_schema := self._try_model_dependency_schema(prop_name, model):
            return dep_schema

        if dep_schema := self._try_function_tool_dependency(prop_schema, prop_name):
            return dep_schema

        return self._schema_processor.process_schema_by_type(
            prop_schema,
            inline_model_refs=inline_model_refs,
        )

    def _try_model_dependency_schema(
        self,
        prop_name: str,
        model: type[BaseModel],
    ) -> JsonObject | None:
        """Try to detect dependency from model decorator."""
        dependencies = _get_attached_dependencies(model)
        return self._dependency_detector.detect_dependency(
            dependencies=dependencies,
            arg_name=prop_name,
            context_name=model.__name__,
        )

    # MARK: - Function Tool Dependencies

    def _try_function_tool_dependency(
        self,
        prop_schema: JsonObject,
        prop_name: str,
    ) -> JsonObject | None:
        """Try to create function tool dependency if applicable."""
        if not self._is_function_tool_candidate(prop_schema):
            return None

        function_tool = self._find_matching_function_tool(prop_name)
        if not function_tool:
            return None

        tool_id = create_uuid(function_tool)
        tool_name = _require_callable_name(function_tool)

        return {
            "type": "tool_dependency",
            "tool_id": tool_id,
            "tool_name": tool_name,
            "tool_type": "func",
            "description": f"Output from {tool_name}",
            "output_type": "object",
        }

    def _is_function_tool_candidate(self, prop_schema: JsonObject) -> bool:
        """Check the legacy fallback contract for inferred function dependencies.

        Explicit dependency decorators win first. This fallback only applies to
        free-form object properties whose descriptions say they carry calculated
        output and whose names can be stem-matched to a registered function tool.
        """
        if prop_schema.get("type") != "object":
            return False
        if prop_schema.get("additionalProperties") is not True:
            return False

        description_value = prop_schema.get("description", "")
        description = description_value.lower() if isinstance(description_value, str) else ""

        return any(keyword in description for keyword in _FUNCTION_TOOL_CANDIDATE_KEYWORDS)

    def _find_matching_function_tool(self, prop_name: str) -> FunctionToolCallable | None:
        """Find a function tool that matches this property."""
        prop_normalized = _remove_tokens(prop_name, _FUNCTION_TOOL_PROPERTY_TOKENS)

        for tool in self._available_function_tools:
            tool_name = _get_callable_name(tool)
            if tool_name is None:
                continue

            tool_normalized = _remove_tokens(tool_name, _FUNCTION_TOOL_NAME_TOKENS)

            if prop_normalized in tool_name.lower() or tool_normalized in prop_name.lower():
                return tool

        return None

    # MARK: - Data Dependency Separation

    def _separate_data_dependencies(
        self,
        all_properties: JsonObject,
    ) -> tuple[JsonObject, list[str]]:
        """Separate data_dependency fields from regular properties."""
        properties: JsonObject = {}
        data_dep_fields: list[str] = []

        for prop_name, prop_schema in all_properties.items():
            prop_object = cast(JsonObject, prop_schema) if isinstance(prop_schema, dict) else None
            if prop_object is not None and prop_object.get("type") == "data_dependency":
                data_dep_fields.append(prop_name)
            properties[prop_name] = prop_schema

        return properties, data_dep_fields

    # MARK: - Primitive Schema Building

    def _build_model_dependency(self, model: type[BaseModel]) -> JsonObject:
        """Build tool dependency schema for a Pydantic model."""
        tool_id = self.get_tool_id_for_model(model)
        return self._dependency_detector.build_model_tool_dependency(
            tool_id=tool_id,
            model_name=model.__name__,
        )

    def _build_primitive_schema(
        self,
        annotation: object,
        is_return: bool = False,
    ) -> JsonObject:
        """Build schema for primitive/non-model types.

        Honors :pep:`593` ``Annotated`` metadata:

        * ``Annotated[T, "description"]`` — a bare string is captured as the
          parameter's ``description`` (useful shorthand).
        * ``Annotated[T, Field(description=..., min_length=..., gt=..., ...)]``
          — full Pydantic ``Field`` constraints flow through verbatim via
          Pydantic's :class:`TypeAdapter`.

        These descriptions and constraints land in the JSON schema that the
        LLM sees, which is the high-leverage place to clarify what each
        argument means (e.g. "a single vehicle_id string, not the whole
        list").
        """
        bare_description = self._extract_bare_str_description(annotation)
        try:
            adapter: TypeAdapter[object] = TypeAdapter(annotation)
            schema = cast(JsonObject, adapter.json_schema())
            _ = schema.pop("$defs", None)
            if bare_description and "description" not in schema:
                schema["description"] = bare_description
            return schema
        except Exception as e:  # noqa: BLE001 - Pydantic schema generation is best-effort.
            if is_return:
                return {
                    "type": "object",
                    "description": bare_description or "Complex return type",
                    "note": f"Schema generation failed: {e!s}",
                }
            return {
                "type": "string",
                "description": bare_description or f"Complex type: {annotation}",
                "note": f"Schema generation failed: {e!s}",
            }

    @staticmethod
    def _extract_bare_str_description(annotation: object) -> str | None:
        """Return the first bare ``str`` in ``Annotated`` metadata, if any.

        Pydantic ignores bare strings inside ``Annotated`` (it only picks up
        :class:`pydantic.fields.FieldInfo`), so we extract them ourselves
        and use the value as a fallback description.
        """
        if get_origin(annotation) is not Annotated:
            return None
        for arg in cast(tuple[object, ...], get_args(annotation))[1:]:
            if isinstance(arg, str):
                return arg
        return None

    def _resolve_tool_id(self, model_name: str) -> str:
        """Resolve tool ID for a model name."""
        model_class = self._model_classes.get(model_name)
        if model_class is not None:
            return self.get_tool_id_for_model(model_class)
        return f"placeholder-{model_name.lower()}-tool-id"


# MARK: Helpers


def _get_attached_dependencies(target: object) -> list[BaseDependency]:
    """Return decorator-attached dependencies from the dynamic metadata slot."""
    return cast(list[BaseDependency], getattr(target, "_dependencies", []) or [])


def _get_callable_name(target: object) -> str | None:
    name = getattr(target, "__name__", None)
    return name if isinstance(name, str) else None


def _require_callable_name(target: object) -> str:
    name = _get_callable_name(target)
    if name is None:
        raise AttributeError("Callable is missing __name__")
    return name


def _get_doc(target: object) -> str:
    doc = getattr(target, "__doc__", None)
    return doc if isinstance(doc, str) else ""


def _get_object_value(schema: JsonObject, key: str) -> JsonObject:
    value = schema.get(key)
    return cast(JsonObject, value) if isinstance(value, dict) else {}


def _get_str_list_value(schema: JsonObject, key: str) -> list[str]:
    value = schema.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _contains_local_ref(value: object) -> bool:
    if isinstance(value, dict):
        value_obj = cast(dict[str, object], value)
        ref = value_obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return True
        return any(_contains_local_ref(item) for item in value_obj.values())

    if isinstance(value, list):
        items = cast(list[object], value)
        return any(_contains_local_ref(item) for item in items)

    return False


def _remove_tokens(value: str, tokens: tuple[str, ...]) -> str:
    normalized = value
    for token in tokens:
        normalized = normalized.replace(token, "")
    return normalized


__all__ = ["SchemaBuilder"]
