"""ToolSpecFactory for creating flattened ToolSpecs.

Flattens nested Pydantic models into separate ToolSpec instances with dependencies.
Includes deduplication to avoid duplicate ToolSpec creation.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from maivn_shared import ToolSpec
from pydantic import JsonValue

from maivn._internal.core.entities.tools import (
    FunctionTool,
    McpTool,
    MethodTool,
    ModelTool,
)

from .flattener import ToolFlattener

if TYPE_CHECKING:
    from maivn_shared import BaseDependency

    from maivn._internal.core.entities import BaseTool

JsonObject = dict[str, JsonValue]


# MARK: Exceptions


class _UnsupportedToolTypeError(TypeError, ValueError):
    """Unsupported tool error that preserves the legacy ValueError catch path."""


# MARK: ToolSpecFactory


class ToolSpecFactory:
    """Factory for creating flattened ToolSpecs with deduplication.

    This factory creates ToolSpecs where nested Pydantic models are flattened
    into separate ToolSpec instances with explicit dependencies in args_schema.
    Includes per-instance deduplication cache and function tool registry.
    """

    # MARK: - Initialization

    def __init__(self) -> None:
        """Initialize the factory."""
        self._tool_flattener: ToolFlattener = ToolFlattener()
        self._created_specs: dict[str, ToolSpec] = {}
        self._function_tool_registry: list[Callable[..., object]] = []

    def reset_cache(self) -> None:
        """Reset per-instance caches.

        ToolSpecs must reflect the current compilation context (e.g., structured_output
        can override final_tool behavior), so this factory cannot retain ToolSpecs
        across invocations.
        """
        self._created_specs.clear()
        self._function_tool_registry.clear()
        self._tool_flattener.clear_cache()

    # MARK: - Internal Helpers

    def _register_function_tool(self, func: Callable[..., object]) -> None:
        """Register a function tool for dependency detection within this factory instance."""
        if func not in self._function_tool_registry:
            self._function_tool_registry.append(func)
            self._tool_flattener.schema_builder.set_function_tools(self._function_tool_registry)

    def _deduplicate_specs(self, specs: list[ToolSpec]) -> list[ToolSpec]:
        """Deduplicate specs within this factory instance."""
        deduplicated: list[ToolSpec] = []
        for spec in specs:
            cached = self._created_specs.get(spec.tool_id)
            if cached is None:
                self._created_specs[spec.tool_id] = spec
                cached = spec
            deduplicated.append(cached)
        return deduplicated

    # MARK: - Public API

    def create(
        self,
        *,
        agent_id: str,
        tool: BaseTool,
        dependencies: list[BaseDependency] | None = None,
        always_execute: bool = False,
        final_tool: bool = False,
    ) -> ToolSpec:
        """Create a flattened ToolSpec for a tool.

        Args:
            agent_id: Identifier for the agent executing the tool
            tool: The tool model to create an execution caller for
            dependencies: Optional list of dependencies to attach to the tool
            always_execute: Whether this tool should always execute
            final_tool: Whether this is the final tool in the workflow

        Returns:
            The main ToolSpec (flattened dependencies are embedded in args_schema)

        Raises:
            ValueError: If no ToolSpecs could be created for the tool
        """
        specs = self.create_all(
            agent_id=agent_id,
            tool=tool,
            dependencies=dependencies,
            always_execute=always_execute,
            final_tool=final_tool,
        )
        if not specs:
            raise ValueError(f"No ToolSpecs could be created for tool: {tool}")
        return specs[-1]

    def create_all(
        self,
        *,
        agent_id: str,
        tool: BaseTool,
        dependencies: list[BaseDependency] | None = None,
        always_execute: bool = False,
        final_tool: bool = False,
    ) -> list[ToolSpec]:
        """Create all flattened ToolSpecs for a tool with deduplication.

        Args:
            agent_id: Identifier for the agent executing the tool
            tool: The tool model to create execution callers for
            dependencies: Optional list of dependencies to attach to the tool
            always_execute: Whether this tool should always execute
            final_tool: Whether this is the final tool in the workflow

        Returns:
            List of all flattened ToolSpec instances including dependencies

        Raises:
            ValueError: If the tool type is not supported
        """
        if dependencies is not None:
            tool.dependencies = dependencies

        if isinstance(tool, MethodTool):
            specs = self._create_method_tool_specs(
                tool=tool,
                agent_id=agent_id,
                always_execute=always_execute,
                final_tool=final_tool,
            )
        elif isinstance(tool, FunctionTool):
            specs = self._create_function_tool_specs(
                tool=tool,
                agent_id=agent_id,
                always_execute=always_execute,
                final_tool=final_tool,
            )
        elif isinstance(tool, ModelTool):
            specs = self._create_model_tool_specs(
                tool=tool,
                agent_id=agent_id,
                always_execute=always_execute,
                final_tool=final_tool,
            )
        elif isinstance(tool, McpTool):
            specs = self._create_mcp_tool_specs(
                tool=tool,
                agent_id=agent_id,
                always_execute=always_execute,
                final_tool=final_tool,
            )
        else:
            raise _UnsupportedToolTypeError(f"Unsupported tool type: {type(tool).__name__}")

        return self._deduplicate_specs(specs)

    # MARK: - Tool Creation

    def _create_function_tool_specs(
        self,
        *,
        tool: FunctionTool,
        agent_id: str,
        always_execute: bool,
        final_tool: bool,
    ) -> list[ToolSpec]:
        """Create ToolSpecs for a function tool."""
        self._register_function_tool(tool.func)

        from maivn._internal.core.entities import AgentTool

        target_agent_id = tool.target_agent_id if isinstance(tool, AgentTool) else None

        return self._tool_flattener.flatten_function_tool(
            func=tool.func,
            agent_id=agent_id,
            name=tool.name,
            description=tool.description,
            always_execute=always_execute or tool.always_execute,
            final_tool=final_tool or tool.final_tool,
            metadata=tool.metadata,
            tags=tool.tags,
            output_schema=tool.output_schema,
            tool_id=tool.tool_id,
            target_agent_id=target_agent_id,
        )

    def _create_method_tool_specs(
        self,
        *,
        tool: MethodTool,
        agent_id: str,
        always_execute: bool,
        final_tool: bool,
    ) -> list[ToolSpec]:
        """Create ToolSpecs for a method tool.

        Structurally identical to function-tool spec creation: the bound
        method is a regular callable, so the flattener / spec generator
        path applies unchanged. The owner reference on
        :class:`MethodTool` is intentionally not surfaced in the spec —
        it's runtime-only state used for audit and lifecycle, not part
        of the LLM-facing tool definition.
        """
        self._register_function_tool(tool.func)

        return self._tool_flattener.flatten_function_tool(
            func=tool.func,
            agent_id=agent_id,
            name=tool.name,
            description=tool.description,
            always_execute=always_execute or tool.always_execute,
            final_tool=final_tool or tool.final_tool,
            metadata=tool.metadata,
            tags=tool.tags,
            output_schema=tool.output_schema,
            tool_id=tool.tool_id,
            target_agent_id=None,
            tool_type_override="method",
        )

    def _create_model_tool_specs(
        self,
        *,
        tool: ModelTool,
        agent_id: str,
        always_execute: bool,
        final_tool: bool,
    ) -> list[ToolSpec]:
        """Create ToolSpecs for a model tool."""
        if tool.output_schema is not None:
            raise ValueError(
                "ToolOverride(output_schema=...) is not supported for model tools. "
                "Model tools derive their contract from the Pydantic model class."
            )
        return self._tool_flattener.flatten_model_tool(
            model=tool.model,
            agent_id=agent_id,
            name=tool.name,
            description=tool.description,
            always_execute=always_execute or tool.always_execute,
            final_tool=final_tool or tool.final_tool,
            metadata=tool.metadata,
            tags=tool.tags,
        )

    def _create_mcp_tool_specs(
        self,
        *,
        tool: McpTool,
        agent_id: str,
        always_execute: bool,
        final_tool: bool,
    ) -> list[ToolSpec]:
        """Create ToolSpecs for an MCP tool."""
        args_schema = tool.args_schema or cast(JsonObject, {"type": "object", "properties": {}})
        # BaseTool metadata is typed as object-valued; MCP ToolSpecs require JSON-shaped metadata.
        metadata = cast(JsonObject, dict(tool.metadata or {}))
        metadata.update(
            {
                "mcp_server": tool.server_name,
                "mcp_tool_name": tool.mcp_tool_name,
            }
        )
        if tool.default_args:
            metadata["default_args"] = tool.default_args
        if tool.output_schema:
            metadata["output_schema"] = tool.output_schema
        if tool.annotations:
            metadata["annotations"] = tool.annotations

        return [
            ToolSpec(
                tool_id=tool.tool_id,
                agent_id=agent_id,
                name=tool.name,
                description=tool.description,
                tags=tool.tags or [],
                tool_type="mcp",
                args_schema=args_schema,
                always_execute=always_execute or tool.always_execute,
                final_tool=final_tool or tool.final_tool,
                output_schema=tool.output_schema,
                metadata=metadata,
            )
        ]


__all__ = ["ToolSpecFactory"]
