"""Tool flattener for generating ToolSpecs.

Recursively flattens nested Pydantic models into separate ToolSpec objects,
emitting explicit dependency references in generated schemas.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ForwardRef, get_type_hints

from maivn_shared import ToolSpec, create_uuid
from pydantic import BaseModel

from .dependency_extractor import (
    apply_arg_policies_to_schema,
    extract_tool_dependencies,
    merge_metadata,
)
from .schema_builder import SchemaBuilder
from .type_utils import (
    extract_nested_models,
    get_module_globals_for_callable,
    get_module_globals_for_model,
    is_pydantic_model,
    resolve_forward_ref,
    safe_resolve_string_type,
)

# MARK: Tool Flattener


class ToolFlattener:
    """Flattens nested tools into individual ToolSpec objects.

    Handles both function tools and Pydantic model tools, extracting
    nested model dependencies and creating separate ToolSpec instances
    for each with proper dependency references.
    """

    def __init__(self) -> None:
        """Initialize the tool flattener."""
        self.schema_builder = SchemaBuilder()
        self._processed_tools: dict[str, ToolSpec] = {}

    # MARK: - Public API

    def flatten_function_tool(
        self,
        func: Callable[..., Any],
        agent_id: str,
        name: str | None = None,
        description: str | None = None,
        always_execute: bool = False,
        final_tool: bool = False,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        tool_id: str | None = None,
        target_agent_id: str | None = None,
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

        func_schema = self.schema_builder.create_from_function(func, resolved_tool_id)
        is_agent_tool = bool(target_agent_id) or bool(tags and "agent_invocation" in tags)
        tool_type = "agent" if is_agent_tool else "func"

        resolved_metadata = dict(metadata or {})
        if target_agent_id:
            resolved_metadata["target_agent_id"] = target_agent_id
        apply_arg_policies_to_schema(func_schema, resolved_metadata)

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
        metadata: dict[str, Any] | None = None,
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

        tool_specs: list[ToolSpec] = []

        nested_models = self._extract_nested_models_from_class(model)
        for nested_model in nested_models:
            nested_tool_specs = self.flatten_model_tool(
                nested_model,
                agent_id,
                always_execute=always_execute,
                final_tool=False,
                tags=tags,
            )
            tool_specs.extend(nested_tool_specs)

        model_schema = self.schema_builder.create_from_model(model, model_tool_id)
        resolved_metadata = dict(metadata or {})
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

    def get_processed_tools(self) -> dict[str, ToolSpec]:
        """Get all processed tools by tool_id."""
        return self._processed_tools.copy()

    def clear_cache(self) -> None:
        """Clear the processed tools cache."""
        self._processed_tools.clear()
        self.schema_builder = SchemaBuilder()

    # MARK: - Static Dependency Extraction

    @staticmethod
    def extract_tool_dependencies(args_schema: dict[str, Any]) -> list[dict[str, Any]]:
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
        metadata: dict[str, Any] | None,
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
            cached.metadata = merge_metadata(cached.metadata, metadata)

    # MARK: - Model Extraction

    def _extract_model_dependencies_from_function(
        self, func: Callable[..., Any]
    ) -> list[type[BaseModel]]:
        """Extract Pydantic model classes from function signature."""
        models: list[type[BaseModel]] = []
        module_globals = get_module_globals_for_callable(func)

        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}

        for param_name, param_type in hints.items():
            if param_name == "return":
                continue
            param_models = self._resolve_models_from_annotation(param_type, module_globals)
            models.extend(param_models)

        if "return" in hints:
            return_models = self._resolve_models_from_annotation(hints["return"], module_globals)
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
            field_type = resolved_hints.get(field_name, field_info.annotation)

            if isinstance(field_type, str | ForwardRef):
                resolved_type = resolve_forward_ref(field_type, module_globals)
                if resolved_type is not None:
                    field_type = resolved_type

            field_models = self._resolve_models_from_annotation(field_type, module_globals)
            nested_models.extend(field_models)

        return _deduplicate_models(nested_models)

    def _resolve_models_from_annotation(
        self, annotation: Any, module_globals: dict[str, Any]
    ) -> list[type[BaseModel]]:
        """Resolve Pydantic models from a type annotation."""
        extracted_models = extract_nested_models(annotation)
        resolved_models = []

        for model in extracted_models:
            if isinstance(model, str):
                resolved_model = safe_resolve_string_type(model, module_globals)
                if resolved_model and is_pydantic_model(resolved_model):
                    resolved_models.append(resolved_model)
            else:
                resolved_models.append(model)

        return resolved_models


# MARK: Helpers


def _get_resolved_type_hints(model: type[BaseModel]) -> dict[str, Any]:
    """Get resolved type hints for a model, handling errors gracefully."""
    try:
        return get_type_hints(model)
    except Exception:
        return {}


def _deduplicate_models(models: list[type[BaseModel]]) -> list[type[BaseModel]]:
    """Remove duplicate models while preserving order."""
    seen: set[type[BaseModel]] = set()
    unique_models: list[type[BaseModel]] = []

    for model in models:
        if model not in seen:
            seen.add(model)
            unique_models.append(model)

    return unique_models


__all__ = ["ToolFlattener"]
