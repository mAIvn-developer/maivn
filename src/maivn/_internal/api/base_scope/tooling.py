"""Tooling and registration helpers for ``BaseScope``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from maivn_shared import PrivateData, RedactedMessage, RedactionPreviewResponse
from pydantic import BaseModel

from maivn._internal.api.mcp import MCPServer
from maivn._internal.core.entities.tools import BaseTool, FunctionTool, McpTool, ModelTool
from maivn._internal.core.interfaces.repositories import (
    DependencyRepoInterface,
    ToolRepoInterface,
)
from maivn._internal.core.interfaces.resolvers import ScopeResolverInterface
from maivn._internal.core.registrars import ToolRegistrar
from maivn._internal.core.services.toolify import ToolifyOptions, ToolifyService

from .builders import (
    EventInvocationBuilder,
    StructuredOutputInvocationBuilder,
    ToolifyDecoratorBuilder,
)
from .mcp import McpRegistry
from .redaction import preview_redaction as _preview_redaction
from .validation import (
    raise_validation_error,
    validate_swarm_final_output_agents,
    validate_tool_flags_per_scope,
)

# MARK: Scope Tooling


class BaseScopeToolingMixin:
    _tool_repo: ToolRepoInterface
    _dependency_repo: DependencyRepoInterface
    _resolver: ScopeResolverInterface
    _tool_registrar: ToolRegistrar
    _toolify_service: ToolifyService
    _compiled_tools_cache: list[FunctionTool | ModelTool | McpTool] | None
    _tools_dirty: bool
    _mcp_registry: McpRegistry

    def toolify(
        self,
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: Callable[[dict[str, Any]], Any] | None = None,
        after_execute: Callable[[dict[str, Any]], Any] | None = None,
    ) -> ToolifyDecoratorBuilder:
        options = ToolifyOptions(
            name=name,
            description=description,
            always_execute=always_execute,
            final_tool=final_tool,
            tags=tags,
            before_execute=before_execute,
            after_execute=after_execute,
        )
        return ToolifyDecoratorBuilder(scope=self, options=options)

    def add_tool(
        self,
        tool: BaseTool | Callable[..., Any] | type[BaseModel],
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: Callable[[dict[str, Any]], Any] | None = None,
        after_execute: Callable[[dict[str, Any]], Any] | None = None,
    ) -> BaseTool:
        """Register a callable, Pydantic model, or prebuilt tool on this scope."""
        options = ToolifyOptions(
            name=name,
            description=description,
            always_execute=always_execute,
            final_tool=final_tool,
            tags=tags,
            before_execute=before_execute,
            after_execute=after_execute,
        )
        registered_tool = self._coerce_tool_for_registration(tool, options)
        callback_source = self._get_tool_callback_source(tool=tool, registered_tool=registered_tool)

        self._toolify_service.register_tool(
            tool=registered_tool,
            registrar=self._tool_registrar,
            dependency_repo=self._dependency_repo,
        )
        self._toolify_service.setup_dependency_callback(
            obj=callback_source,
            tool=registered_tool,
            dependency_repo=self._dependency_repo,
        )
        self._attach_registered_tool_id(callback_source, registered_tool)
        self._tools_dirty = True
        return registered_tool

    def structured_output(self, model: type[BaseModel]) -> StructuredOutputInvocationBuilder:
        return StructuredOutputInvocationBuilder(scope=self, model=model)

    def events(
        self,
        *,
        include: Sequence[str] | str | None = None,
        exclude: Sequence[str] | str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        auto_verbose: bool = True,
    ) -> EventInvocationBuilder:
        """Build an invocation wrapper with filtered event reporting."""
        return EventInvocationBuilder(
            scope=self,
            include=include,
            exclude=exclude,
            on_event=on_event,
            auto_verbose=auto_verbose,
        )

    def preview_redaction(
        self,
        message: RedactedMessage,
        *,
        known_pii_values: list[str | PrivateData] | None = None,
        private_data: dict[str, Any] | None = None,
    ) -> RedactionPreviewResponse:
        return _preview_redaction(
            self,
            message,
            known_pii_values=known_pii_values,
            private_data=private_data,
        )

    # MARK: - MCP Server Registration

    def register_mcp_servers(self, servers: MCPServer | Sequence[MCPServer]) -> None:
        """Register MCP servers and expose their tools in this scope."""
        self._mcp_registry.register_servers(servers)

    def list_mcp_servers(self) -> list[MCPServer]:
        """List MCP servers registered with this scope."""
        return self._mcp_registry.list_servers()

    def close_mcp_servers(self) -> None:
        """Close all MCP server connections for this scope."""
        private_state = getattr(self, "__pydantic_private__", None)
        if not isinstance(private_state, dict):
            return
        registry = private_state.get("_mcp_registry")
        if registry is None:
            return
        registry.close_servers()

    # MARK: - Tool Access

    def get_tool(self, tool_id: str) -> BaseTool | None:
        return self._tool_repo.get_tool(tool_id)

    def list_tools(self) -> list[BaseTool]:
        return self._tool_repo.list_tools()

    # MARK: - Tool Compilation

    def compile_tools(self) -> list[FunctionTool | ModelTool | McpTool]:
        if not self._tools_dirty and self._compiled_tools_cache is not None:
            return self._compiled_tools_cache

        compiled = self._compile_all_tools()
        self._compiled_tools_cache = compiled
        self._tools_dirty = False
        return compiled

    def _compile_all_tools(self) -> list[FunctionTool | ModelTool | McpTool]:
        compiled: list[FunctionTool | ModelTool | McpTool] = []
        for tool in self._tool_repo.list_tools():
            if isinstance(tool, (FunctionTool, ModelTool)):
                self._resolve_tool_dependencies(tool)
                compiled.append(tool)
            elif isinstance(tool, McpTool):
                compiled.append(tool)
        return compiled

    def _resolve_tool_dependencies(self, tool: FunctionTool | ModelTool) -> None:
        raw_deps = list(getattr(tool, "dependencies", []) or [])
        if not raw_deps:
            repo_tool_id = getattr(tool, "tool_id", "")
            raw_deps = list(self._dependency_repo.list_dependencies(repo_tool_id))
        tool.dependencies = [self._resolver.resolve(dep) for dep in raw_deps]
        self._tool_repo.update_tool(tool)

    # MARK: - Validation

    def validate_tool_configuration(self) -> None:
        errors = validate_tool_flags_per_scope(self)
        errors.extend(validate_swarm_final_output_agents(self))

        if errors:
            raise_validation_error(errors)

    # MARK: - Registration Helpers

    def _coerce_tool_for_registration(
        self,
        tool: BaseTool | Callable[..., Any] | type[BaseModel],
        options: ToolifyOptions,
    ) -> BaseTool:
        if isinstance(tool, BaseTool):
            self._apply_existing_tool_options(tool, options)
            return tool

        return self._toolify_service.create_tool(tool, options)

    @staticmethod
    def _apply_existing_tool_options(tool: BaseTool, options: ToolifyOptions) -> None:
        if options.name is not None:
            tool.name = options.name
        if options.description is not None:
            tool.description = options.description
        if options.always_execute:
            tool.always_execute = True
        if options.final_tool:
            tool.final_tool = True
        if options.tags:
            tool.tags = list(options.tags)
        if options.before_execute is not None:
            tool.before_execute = options.before_execute
        if options.after_execute is not None:
            tool.after_execute = options.after_execute

    @staticmethod
    def _get_tool_callback_source(
        *,
        tool: BaseTool | Callable[..., Any] | type[BaseModel],
        registered_tool: BaseTool,
    ) -> Any:
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(registered_tool, FunctionTool):
            return registered_tool.func
        if isinstance(registered_tool, ModelTool):
            return registered_tool.model
        return registered_tool

    @staticmethod
    def _attach_registered_tool_id(obj: Any, tool: BaseTool) -> None:
        tool_id = getattr(tool, "tool_id", None)
        if tool_id is None:
            return
        try:
            obj.tool_id = tool_id
        except Exception:
            pass
