"""ToolSpecFactory for creating flattened ToolSpecs.

Flattens nested Pydantic models into separate ToolSpec instances with dependencies.
Includes deduplication to avoid duplicate ToolSpec creation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maivn_shared import ToolSpec

from maivn._internal.core.entities.tools import FunctionTool, McpTool, ModelTool

from .flattener import ToolFlattener

if TYPE_CHECKING:
    from maivn_shared import BaseDependency

    from maivn._internal.core.entities import BaseTool


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
        self._tool_flattener = ToolFlattener()
        self._created_specs: dict[str, ToolSpec] = {}
        self._function_tool_registry: list[Callable] = []

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

    def _register_function_tool(self, func: Callable) -> None:
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

        if isinstance(tool, FunctionTool):
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
            raise ValueError(f"Unsupported tool type: {type(tool)}")

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
            tool_id=tool.tool_id,
            target_agent_id=target_agent_id,
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
        args_schema = tool.args_schema or {"type": "object", "properties": {}}
        metadata: dict[str, Any] = {
            "mcp_server": tool.server_name,
            "mcp_tool_name": tool.mcp_tool_name,
        }
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
                metadata=metadata,
            )
        ]


__all__ = ["ToolSpecFactory"]
