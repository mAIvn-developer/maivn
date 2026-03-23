"""Enhanced tool execution service with dependency handling and hooks.

Extends ``BasicToolExecutionService`` with input validation, dependency
resolution, strategy-based dispatch, and before/after execution hooks.
"""

from __future__ import annotations

from typing import Any

from maivn_shared.infrastructure.logging import MetricsLoggerProtocol

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.services.dependency_execution_service import (
    DependencyExecutionService,
)

from ..helpers import InputValidator, PydanticDeserializer
from .argument_utils import prune_arguments
from .basic_tool_execution_service import BasicToolExecutionService, ToolType
from .execution_strategy import StrategyRegistry, create_default_registry
from .helpers import DependencyResolver

# MARK: Enhanced Execution


class ToolExecutionService(BasicToolExecutionService):
    """Tool execution service with dependency resolution and strategy-based dispatch.

    This service orchestrates the execution of tools by:
    - Validating input arguments
    - Resolving dependencies (agent, user, tool)
    - Dispatching execution via the strategy registry
    - Running before/after execution hooks
    """

    def __init__(
        self,
        *,
        logger: MetricsLoggerProtocol | None = None,
        dependency_execution_service: DependencyExecutionService | None = None,
        pydantic_deserializer: PydanticDeserializer | None = None,
        dependency_resolver: DependencyResolver | None = None,
        strategy_registry: StrategyRegistry | None = None,
        input_validator: type[InputValidator] | None = None,
    ) -> None:
        super().__init__(logger=logger)

        dependency_service = dependency_execution_service or DependencyExecutionService(
            logger=logger
        )
        deserializer = pydantic_deserializer or PydanticDeserializer(logger=logger)

        self._dependency_resolver = dependency_resolver or DependencyResolver(
            logger=logger,
            dependency_service=dependency_service,
        )
        self._strategy_registry = strategy_registry or create_default_registry(
            logger=logger,
            deserializer=deserializer,
        )
        self._dependency_service = dependency_service
        self._input_validator = input_validator or InputValidator

    # MARK: - Execution

    def execute_tool_call(
        self,
        tool_id: str,
        args: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> Any:
        """Execute a tool call with dependency resolution and input validation.

        Args:
            tool_id: Tool identifier to execute
            args: Arguments for tool execution
            context: Execution context (scope, messages, etc.)

        Returns:
            Tool execution result

        Raises:
            ValueError: If input validation fails
        """
        context = context or ExecutionContext()

        tool = self.resolve_tool(tool_id)
        validated_args = self._validate_arguments(tool_id, tool, args)

        self._logger.debug("[TOOL_EXEC] Tool type: %s", type(tool).__name__)

        resolved_args = self._resolve_dependencies(tool, validated_args, context)
        filtered_args = self._filter_arguments(tool_id, tool, resolved_args)

        self._run_execution_hooks(
            stage="before",
            tool_id=tool_id,
            tool=tool,
            args=filtered_args,
            context=context,
            result=None,
            error=None,
        )

        try:
            result = self._strategy_registry.execute(tool, filtered_args, context)
        except Exception as exc:
            self._run_execution_hooks(
                stage="after",
                tool_id=tool_id,
                tool=tool,
                args=filtered_args,
                context=context,
                result=None,
                error=exc,
            )
            raise

        self._run_execution_hooks(
            stage="after",
            tool_id=tool_id,
            tool=tool,
            args=filtered_args,
            context=context,
            result=result,
            error=None,
        )
        return result

    # MARK: - Validation

    def _validate_arguments(
        self,
        tool_id: str,
        tool: ToolType,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate input arguments for security."""
        if getattr(tool, "tool_type", None) == "agent":
            self._logger.debug("[TOOL_EXEC] Skipping input validation for agent tool %s", tool_id)
            return args
        try:
            validated_args = self._input_validator.validate_tool_arguments(args)
            self._logger.debug("[TOOL_EXEC] Input validation passed for %s", tool_id)
            return validated_args
        except ValueError as e:
            self._logger.error("[TOOL_EXEC] Input validation failed for %s: %s", tool_id, e)
            raise

    # MARK: - Dependency Resolution

    def _resolve_dependencies(
        self,
        tool: ToolType,
        args: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """Resolve tool dependencies if needed."""
        dependencies = getattr(tool, "dependencies", None)

        self._logger.debug("[TOOL_EXEC] Tool has dependencies: %s", bool(dependencies))
        if dependencies:
            self._logger.debug("[TOOL_EXEC] Dependency count: %d", len(dependencies))
            self._logger.debug("[TOOL_EXEC] Args keys: %s", list(args.keys()))

        if dependencies and self._dependency_resolver.needs_resolution(dependencies, args):
            self._logger.debug("[TOOL_EXEC] Dependencies need resolution - resolving...")
            resolved_args = self._dependency_resolver.resolve_all(
                tool=tool,
                args=args,
                dependencies=dependencies,
                context=context,
                executor=self,
            )
            self._logger.debug(
                "[TOOL_EXEC] Dependencies resolved, args now: %s",
                list(resolved_args.keys()),
            )
            return resolved_args

        self._logger.debug("[TOOL_EXEC] Dependencies already resolved or not needed")
        return args.copy()

    # MARK: - Argument Filtering

    def _filter_arguments(
        self,
        tool_id: str,
        tool: ToolType,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Filter arguments to only those accepted by the tool."""
        filtered_args, dropped_keys = prune_arguments(tool, args, self._logger)
        if dropped_keys:
            self._logger.debug(
                "[TOOL_EXEC] Dropped unexpected args for %s: %s",
                tool_id,
                dropped_keys,
            )
        return filtered_args

    # MARK: - Execution Hooks

    def _run_execution_hooks(
        self,
        *,
        stage: str,
        tool_id: str,
        tool: ToolType,
        args: dict[str, Any],
        context: ExecutionContext,
        result: Any,
        error: Exception | None,
    ) -> None:
        """Run before/after execution hooks from tool, scope, and swarm."""
        hooks = self._collect_hooks(stage, tool, context)
        payload = {
            "stage": stage,
            "tool_id": tool_id,
            "tool": tool,
            "args": args,
            "context": context,
            "result": result,
            "error": error,
        }

        for hook in hooks:
            if hook is None:
                continue
            try:
                hook(payload)
            except Exception:
                self._logger.exception("[TOOL_EXEC] Execution hook failed")

    def _collect_hooks(
        self,
        stage: str,
        tool: ToolType,
        context: ExecutionContext,
    ) -> list[Any]:
        """Collect hooks to run based on stage and hook execution modes."""
        scope = getattr(context, "scope", None)
        swarm = self._get_swarm_from_scope(scope)

        scope_mode = getattr(scope, "hook_execution_mode", "tool")
        swarm_mode = getattr(swarm, "hook_execution_mode", "tool")

        tool_type = getattr(tool, "tool_type", None)
        include_swarm_hooks = swarm_mode == "tool" or (
            swarm_mode == "agent" and tool_type == "agent"
        )

        tool_before = getattr(tool, "before_execute", None)
        tool_after = getattr(tool, "after_execute", None)
        scope_before = getattr(scope, "before_execute", None)
        scope_after = getattr(scope, "after_execute", None)
        swarm_before = getattr(swarm, "before_execute", None)
        swarm_after = getattr(swarm, "after_execute", None)

        if stage == "before":
            hooks: list[Any] = []
            if swarm_before is not None and include_swarm_hooks:
                hooks.append(swarm_before)
            if scope_before is not None and scope_mode == "tool":
                hooks.append(scope_before)
            if tool_before is not None:
                hooks.append(tool_before)
            return hooks

        # stage == 'after'
        hooks = []
        if tool_after is not None:
            hooks.append(tool_after)
        if scope_after is not None and scope_mode == "tool":
            hooks.append(scope_after)
        if swarm_after is not None and include_swarm_hooks:
            hooks.append(swarm_after)
        return hooks

    def _get_swarm_from_scope(self, scope: Any) -> Any:
        """Get swarm object from scope if available."""
        get_swarm = getattr(scope, "get_swarm", None)
        if callable(get_swarm):
            return get_swarm()
        return None

    # MARK: - Service Configuration

    def set_agent_registry(self, registry: Any) -> None:
        """Set agent registry for dependency resolution."""
        self._dependency_service.set_agent_registry(registry)

    def set_interrupt_service(self, service: Any) -> None:
        """Set interrupt service for dependency resolution."""
        self._dependency_service.set_interrupt_service(service)


__all__ = ["ToolExecutionService"]
