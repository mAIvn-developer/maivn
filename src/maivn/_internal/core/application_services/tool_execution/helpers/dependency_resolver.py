"""Dependency resolution for tool execution.

This module handles resolving various dependency types:
- Agent dependencies (via dynamic invocation tools)
- User dependencies (via dynamic user input tools)
- Tool dependencies (from execution context)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maivn_shared import AgentDependency, InterruptDependency, ToolDependency
from maivn_shared.infrastructure.logging import LoggerProtocol
from maivn_shared.utils.prompt_utils import load_prompt

from maivn._internal.core.entities import FunctionTool, McpTool, ModelTool
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.exceptions import (
    AgentNotFoundError,
    DependencyResolutionError,
    ToolDependencyNotFoundError,
)
from maivn._internal.core.services.dependency_execution_service import DependencyExecutionService

if TYPE_CHECKING:
    from ..tool_execution_service import ToolExecutionService


class DependencyResolver:
    """Resolves tool dependencies before execution."""

    def __init__(
        self,
        *,
        logger: LoggerProtocol | None = None,
        dependency_service: DependencyExecutionService | None = None,
    ) -> None:
        """Initialize dependency resolver.

        Args:
            logger: Logger for operation tracking
            dependency_service: Service for executing dependencies
        """
        self._logger: LoggerProtocol | None = logger
        self._dependency_service = dependency_service or DependencyExecutionService()

    # MARK: - Public API

    def needs_resolution(self, dependencies: list[Any], args: dict[str, Any]) -> bool:
        """Check if dependencies need to be resolved or if args already have values.

        If all dependency arg_names are already present in args with non-None values,
        we assume they were resolved server-side and skip SDK-side resolution.
        Agent dependencies are always resolved locally to guarantee up-to-date results.

        Args:
            dependencies: List of dependencies
            args: Current arguments

        Returns:
            True if dependencies need resolution, False if already resolved
        """
        for dependency in dependencies:
            if isinstance(dependency, AgentDependency):
                return True
            arg_name = getattr(dependency, "arg_name", None)
            if arg_name:
                # If arg is missing or None, we need to resolve it
                if arg_name not in args or args[arg_name] is None:
                    return True
        # All dependency args are present with values, skip resolution
        return False

    def resolve_all(
        self,
        tool: FunctionTool | ModelTool | McpTool,
        args: dict[str, Any],
        dependencies: list[Any],
        context: ExecutionContext,
        executor: ToolExecutionService,
    ) -> dict[str, Any]:
        """Resolve all tool dependencies and inject them into arguments.

        Args:
            tool: Tool being executed
            args: Original arguments
            dependencies: List of dependencies to resolve
            context: Execution context
            executor: Tool execution service (for recursive calls)

        Returns:
            Arguments with dependency results injected

        Raises:
            ValueError: If dependency resolution fails
        """
        resolved_args = args.copy()

        for dependency in dependencies:
            tool_identifier = self._get_tool_identifier(tool)
            arg_name = getattr(dependency, "arg_name", None)
            try:
                # Handle each dependency type
                result = self._resolve_single_dependency(dependency, context, executor)

                # Inject the result into the arguments
                if arg_name:
                    resolved_args[arg_name] = result
                    if self._logger:
                        self._logger.debug(
                            "Resolved dependency %s -> %s: %s",
                            type(dependency).__name__,
                            arg_name,
                            str(result)[:100],  # Truncate for logging
                        )

            except Exception as e:
                if self._logger:
                    self._logger.error(
                        "Failed to resolve dependency %s for tool %s: %s",
                        type(dependency).__name__,
                        tool_identifier,
                        str(e),
                    )
                # Re-raise custom exceptions as-is
                if isinstance(
                    e,
                    AgentNotFoundError | ToolDependencyNotFoundError | DependencyResolutionError,
                ):
                    raise
                # Wrap generic exceptions
                raise DependencyResolutionError(
                    dependency_type=type(dependency).__name__,
                    dependency_name=str(arg_name or dependency),
                    details=str(e),
                ) from e

        return resolved_args

    # MARK: - Dependency Resolution

    def _resolve_single_dependency(
        self,
        dependency: Any,
        context: ExecutionContext,
        executor: ToolExecutionService,
    ) -> Any:
        """Resolve a single dependency based on its type.

        Args:
            dependency: Dependency to resolve
            context: Execution context
            executor: Tool execution service

        Returns:
            Resolved dependency value
        """
        if isinstance(dependency, AgentDependency):
            return self._resolve_agent_dependency(dependency, context, executor)
        elif isinstance(dependency, InterruptDependency):
            return self._resolve_user_dependency(dependency, context, executor)
        elif isinstance(dependency, ToolDependency):
            return self._resolve_tool_dependency(dependency, context)
        else:
            # Execute other dependency types via dependency service
            return self._dependency_service.execute_dependency(dependency, context)

    def _resolve_agent_dependency(
        self,
        dependency: AgentDependency,
        context: ExecutionContext,
        executor: ToolExecutionService,
    ) -> Any:
        """Resolve agent dependency by calling the dynamic agent invocation tool.

        Args:
            dependency: Agent dependency to resolve
            context: Execution context
            executor: Tool execution service

        Returns:
            Result from the dynamic agent invocation tool

        Raises:
            ValueError: If dynamic tool cannot be found or executed
        """
        try:
            metadata = context.metadata or {}
            default_prompt = metadata.get(
                "agent_prompt",
                load_prompt(
                    "shared/AGENT_DEPENDENCY_DEFAULT.md",
                    "maivn_internal_shared.prompts",
                ),
            )

            result = executor.execute_tool_call(
                tool_id=dependency.agent_id,
                args={"prompt": default_prompt},
                context=context,
            )

            return result

        except Exception as e:
            if self._logger:
                self._logger.warning(
                    "Dynamic agent tool execution failed for %s, "
                    "falling back to dependency service: %s",
                    dependency.agent_id,
                    e,
                )
            return self._dependency_service.execute_dependency(dependency, context)

    def _resolve_tool_dependency(
        self,
        dependency: ToolDependency,
        context: ExecutionContext,
    ) -> Any:
        """Resolve tool dependency by looking up the result from context.

        Args:
            dependency: Tool dependency to resolve
            context: Execution context containing tool_results

        Returns:
            Result from the dependent tool execution

        Raises:
            ValueError: If the dependent tool result is not found in context
        """
        tool_results = context.tool_results or {}
        tool_id = dependency.tool_id

        if tool_id not in tool_results:
            raise ToolDependencyNotFoundError(
                tool_id=tool_id,
                available_results=list(tool_results.keys()),
            )

        result = tool_results[tool_id]
        if self._logger:
            self._logger.debug(
                "Resolved tool dependency %s: %s",
                tool_id,
                str(result)[:100],  # Truncate for logging
            )
        return result

    def _resolve_user_dependency(
        self,
        dependency: InterruptDependency,
        context: ExecutionContext,
        executor: ToolExecutionService,
    ) -> Any:
        """Resolve user dependency by calling the dynamic user input tool.

        Args:
            dependency: User dependency to resolve
            context: Execution context
            executor: Tool execution service

        Returns:
            Result from the dynamic user input tool

        Raises:
            ValueError: If dynamic tool cannot be found or executed
        """
        prompt_text = getattr(dependency, "prompt", "Please provide input:")
        if self._logger:
            self._logger.info("Using dependency service for user input: %s", prompt_text)
        return self._dependency_service.execute_dependency(dependency, context)

    # MARK: - Utilities

    @staticmethod
    def _get_tool_identifier(tool: FunctionTool | ModelTool | McpTool) -> str:
        """Get a string identifier for a tool.

        Args:
            tool: Tool to get identifier for

        Returns:
            Tool identifier (name, id, or type)
        """
        return (
            getattr(tool, "name", None)
            or getattr(tool, "id", None)
            or getattr(tool, "tool_id", None)
            or type(tool).__name__
        )


__all__ = ["DependencyResolver"]
