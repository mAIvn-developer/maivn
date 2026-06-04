"""Tool flattener for generating ToolSpecs.

Recursively flattens nested Pydantic models into separate ToolSpec objects,
emitting explicit dependency references in generated schemas.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ForwardRef, TypeAlias, cast, get_type_hints

from maivn_shared import ToolSpec, create_uuid
from maivn_shared.domain.entities.tool_spec import ToolType
from pydantic import BaseModel, JsonValue

from maivn._internal.core.services.team_dependencies import apply_team_dependency_arg_schemas

from .dependency_extractor import (
    apply_arg_policies_to_schema,
    extract_tool_dependencies,
    merge_metadata,
)
from .schema_builder import SchemaBuilder
from .type_utils import (
    extract_nested_models,
    get_module_globals_for_model,
    resolve_forward_ref,
)

JsonObject: TypeAlias = dict[str, JsonValue]


# MARK: Tool Flattener


class ToolFlattener:
    """Flattens nested tools into individual ToolSpec objects.

    Handles both function tools and Pydantic model tools, extracting
    nested model dependencies and creating separate ToolSpec instances
    for each with proper dependency references.

    The processed-tool cache is intentionally per instance and keyed by
    deterministic tool_id. Instances are expected to be compilation-local
    and single-threaded; reuse across sessions should call clear_cache().
    """

    def __init__(self) -> None:
        """Initialize the tool flattener."""
        self.schema_builder: SchemaBuilder = SchemaBuilder()
        self._processed_tools: dict[str, ToolSpec] = {}
        self._processing_model_tool_ids: set[str] = set()

    # MARK: - Public API

    def flatten_function_tool(
        self,
        func: Callable[..., object],
        agent_id: str,
        name: str | None = None,
        description: str | None = None,
        always_execute: bool = False,
        final_tool: bool = False,
        metadata: Mapping[str, object] | None = None,
        tags: list[str] | None = None,
        tool_id: str | None = None,
        target_agent_id: str | None = None,
        tool_type_override: ToolType | None = None,
    ) -> list[ToolSpec]:
        """Flatten a function tool into ToolSpecs.

        Args:
            func: The function to flatten
            agent_id: Agent ID that owns this tool
            name: Optional tool name
            description: Optional tool description
            always_execute: Whether this tool must always execute
            final_tool: Whether this tool produces final output
            metadata: Optional tool metadata
            tags: Optional tool tags
            tool_id: Optional pre-generated tool ID (for agent tools)
            target_agent_id: Optional target agent ID for agent tools

        Returns:
            List of ToolSpec objects (function + any nested model dependencies)
        """
        tool_specs: list[ToolSpec] = []

        resolved_tool_id = tool_id or create_uuid(func)
        tool_name = name or getattr(func, "__name__", "unnamed_function")
        tool_description = description or (func.__doc__ or "").strip() or "Function tool"

        model_deps = self._extract_model_dependencies_from_function(func)
        for model_class in model_deps:
            model_tool_specs = self.flatten_model_tool(
                model_class,
                agent_id,
                always_execute=always_execute,
                final_tool=False,
                tags=tags,
            )
            tool_specs.extend(model_tool_specs)

        func_schema = cast(
            JsonObject,
            self.schema_builder.create_from_function(func, resolved_tool_id),
        )
        is_agent_tool = bool(target_agent_id) or bool(tags and "agent_invocation" in tags)
        # Callers that already know the source-entity type (e.g. the
        # method-tool path) pass ``tool_type_override`` so the spec carries
        # the right label downstream. Otherwise we fall back to the
        # existing agent-vs-func auto-detection.
        tool_type: ToolType
        if tool_type_override is not None:
            tool_type = tool_type_override
        elif is_agent_tool:
            tool_type = "agent"
        else:
            tool_type = "func"

        resolved_metadata = _json_metadata(metadata)
        if target_agent_id:
            resolved_metadata["target_agent_id"] = target_agent_id
        apply_arg_policies_to_schema(func_schema, resolved_metadata)
        apply_team_dependency_arg_schemas(func_schema, resolved_metadata)

        func_tool_spec = ToolSpec(
            tool_id=resolved_tool_id,
            agent_id=agent_id,
            name=tool_name,
            description=tool_description,
            tags=tags or [],
            tool_type=tool_type,
            args_schema=func_schema,
            always_execute=always_execute,
            final_tool=final_tool,
            metadata=resolved_metadata,
        )

        tool_specs.append(func_tool_spec)
        self._processed_tools[resolved_tool_id] = func_tool_spec

        return tool_specs

    def flatten_model_tool(
        self,
        model: type[BaseModel],
        agent_id: str,
        name: str | None = None,
        description: str | None = None,
        always_execute: bool = False,
        final_tool: bool = False,
        metadata: Mapping[str, object] | None = None,
        tags: list[str] | None = None,
    ) -> list[ToolSpec]:
        """Flatten a Pydantic model tool into ToolSpecs.

        Args:
            model: The Pydantic model to flatten
            agent_id: Agent ID that owns this tool
            name: Optional tool name
            description: Optional tool description
            always_execute: Whether this tool must always execute
            final_tool: Whether this tool produces final output
            metadata: Optional tool metadata
            tags: Optional tool tags

        Returns:
            List of ToolSpec objects (model + any nested model dependencies)
        """
        model_tool_id = self.schema_builder.get_tool_id_for_model(model)
        if model_tool_id in self._processed_tools:
            cached = self._processed_tools[model_tool_id]
            self._merge_cached_model_spec(
                cached=cached,
                model=model,
                name=name,
                description=description,
                always_execute=always_execute,
                final_tool=final_tool,
                metadata=metadata,
                tags=tags,
            )
            return [cached]

        if model_tool_id in self._processing_model_tool_ids:
            return []

        self._processing_model_tool_ids.add(model_tool_id)
        try:
            tool_specs: list[ToolSpec] = []

            nested_models = self._extract_nested_models_from_class(model)
            for nested_model in nested_models:
                # Nested Pydantic models are schema definitions referenced by the
                # parent (e.g. `VerificationFailure` is a list-item type inside
                # `VerificationReport.failures`); they are NOT independently
                # schedulable tools. Always force `always_execute=False` and
                # `final_tool=False` for nested specs so the assignment_agent's
                # required-coverage validator does not demand them in every plan
                # and the orchestrator's evaluate node does not list them in
                # always_execute_tools. Without this override, registering one
                # tool with always_execute=True silently flags every nested
                # schema as required, causing phantom "missing always_execute
                # tool" warnings and unnecessary re-prompt loops.
                nested_tool_specs = self.flatten_model_tool(
                    nested_model,
                    agent_id,
                    always_execute=False,
                    final_tool=False,
                    tags=tags,
                )
                tool_specs.extend(nested_tool_specs)

            model_schema = cast(
                JsonObject,
                self.schema_builder.create_from_model(model, model_tool_id),
            )
            resolved_metadata = _json_metadata(metadata)
            apply_arg_policies_to_schema(model_schema, resolved_metadata)

            tool_name = name or model.__name__
            tool_description = description or (model.__doc__ or "").strip() or "Model tool"

            model_tool_spec = ToolSpec(
                tool_id=model_tool_id,
                agent_id=agent_id,
                name=tool_name,
                description=tool_description,
                tags=tags or [],
                tool_type="model",
                args_schema=model_schema,
                always_execute=always_execute,
                final_tool=final_tool,
                metadata=resolved_metadata,
            )

            tool_specs.append(model_tool_spec)
            self._processed_tools[model_tool_id] = model_tool_spec

            return tool_specs
        finally:
            self._processing_model_tool_ids.discard(model_tool_id)

    def get_processed_tools(self) -> dict[str, ToolSpec]:
        """Get all processed tools by tool_id."""
        return self._processed_tools.copy()

    def clear_cache(self) -> None:
        """Clear the processed tools cache."""
        self._processed_tools.clear()
        self._processing_model_tool_ids.clear()
        self.schema_builder = SchemaBuilder()

    # MARK: - Static Dependency Extraction

    @staticmethod
    def extract_tool_dependencies(args_schema: JsonObject) -> list[JsonObject]:
        """Extract tool dependencies from a schema.

        Args:
            args_schema: The tool's args_schema

        Returns:
            List of tool dependency information
        """
        return extract_tool_dependencies(args_schema)

    # MARK: - Cache Merging

    def _merge_cached_model_spec(
        self,
        *,
        cached: ToolSpec,
        model: type[BaseModel],
        name: str | None,
        description: str | None,
        always_execute: bool,
        final_tool: bool,
        metadata: Mapping[str, object] | None,
        tags: list[str] | None,
    ) -> None:
        """Promote flags/tags when the same model is flattened multiple times."""
        default_name = model.__name__
        default_description = (model.__doc__ or "").strip() or "Model tool"

        if name and cached.name == default_name and cached.name != name:
            cached.name = name

        if description and cached.description == default_description:
            if cached.description != description:
                cached.description = description

        if final_tool and not cached.final_tool:
            cached.final_tool = True

        if always_execute and not cached.always_execute:
            cached.always_execute = True

        if tags:
            existing_tags = list(cached.tags or [])
            merged_tags = list(dict.fromkeys(existing_tags + tags))
            if merged_tags != existing_tags:
                cached.tags = merged_tags

        if metadata:
            cached.metadata = merge_metadata(cached.metadata, _json_metadata(metadata))

    # MARK: - Model Extraction

    def _extract_model_dependencies_from_function(
        self, func: Callable[..., object]
    ) -> list[type[BaseModel]]:
        """Extract Pydantic model classes from function signature."""
        models: list[type[BaseModel]] = []

        try:
            hints = cast(dict[str, object], get_type_hints(func))
        except Exception:  # noqa: BLE001 - annotation resolution can fail on forward refs.
            hints = {}

        for param_name, param_type in hints.items():
            if param_name == "return":
                continue
            param_models = self._resolve_models_from_annotation(param_type)
            models.extend(param_models)

        if "return" in hints:
            return_models = self._resolve_models_from_annotation(hints["return"])
            models.extend(return_models)

        return _deduplicate_models(models)

    def _extract_nested_models_from_class(self, model: type[BaseModel]) -> list[type[BaseModel]]:
        """Extract nested Pydantic models from a model class."""
        if not hasattr(model, "model_fields"):
            return []

        nested_models: list[type[BaseModel]] = []
        module_globals = get_module_globals_for_model(model)
        resolved_hints = _get_resolved_type_hints(model)

        for field_name, field_info in model.model_fields.items():
            field_type = resolved_hints.get(field_name, cast(object, field_info.annotation))

            if isinstance(field_type, str | ForwardRef):
                resolved_type = resolve_forward_ref(field_type, module_globals)
                if resolved_type is not None:
                    field_type = resolved_type

            field_models = self._resolve_models_from_annotation(field_type)
            nested_models.extend(field_models)

        return _deduplicate_models(nested_models)

    def _resolve_models_from_annotation(self, annotation: object) -> list[type[BaseModel]]:
        """Resolve Pydantic models from a type annotation."""
        return extract_nested_models(annotation)


# MARK: Helpers


def _json_metadata(metadata: Mapping[str, object] | None) -> JsonObject:
    """Convert object-valued metadata to the JSON-shaped ToolSpec boundary type."""
    return cast(JsonObject, dict(metadata or {}))


def _get_resolved_type_hints(model: type[BaseModel]) -> dict[str, object]:
    """Get resolved type hints for a model, handling errors gracefully."""
    try:
        return cast(dict[str, object], get_type_hints(model))
    except Exception:  # noqa: BLE001 - unsupported annotations should not block flattening.
        return {}


def _deduplicate_models(models: list[type[BaseModel]]) -> list[type[BaseModel]]:
    """Remove duplicate models by module-qualified name while preserving order."""
    seen: set[tuple[str, str]] = set()
    unique_models: list[type[BaseModel]] = []

    for model in models:
        key = (model.__module__, model.__qualname__)
        if key not in seen:
            seen.add(key)
            unique_models.append(model)

    return unique_models


__all__ = ["ToolFlattener"]
