"""Dependency execution service for handling all dependency types during tool execution.

This service coordinates the execution of various dependency types including
agent dependencies and interrupt dependencies.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from maivn_shared import (
    AgentDependency,
    BaseDependency,
    BaseMessage,
    DataDependency,
    InterruptDependency,
    ToolDependency,
)

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.utils.logger import ensure_domain_logger

from .agent_execution_service import AgentExecutionService
from .interrupt_service import InterruptService

# MARK: - DependencyExecutionService


class DependencyExecutionService:
    """Service for executing all types of dependencies during tool execution."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        agent_execution_service: AgentExecutionService | None = None,
        interrupt_service: InterruptService | None = None,
        logger: Any | None = None,
    ) -> None:
        """Initialize dependency execution service.

        Args:
            agent_execution_service: Service for executing agent dependencies
            interrupt_service: Service for handling interrupts
            logger: Optional logger
        """
        self._agent_execution_service = agent_execution_service or AgentExecutionService(
            logger=logger
        )
        self._interrupt_service = interrupt_service or InterruptService()
        self._logger = ensure_domain_logger(logger)

    # MARK: - Public Methods

    def execute_dependency(
        self,
        dependency: BaseDependency,
        context: ExecutionContext,
    ) -> Any:
        """Execute a dependency and return the result.

        Args:
            dependency: Dependency to execute
            context: Execution context containing scope data, messages, etc.

        Returns:
            Result of dependency execution

        Raises:
            ValueError: If dependency type is not supported or execution fails
        """
        self._logger.debug(
            "Executing dependency: %s (type: %s, arg: %s)",
            type(dependency).__name__,
            getattr(dependency, "arg_name", "unknown"),
            str(dependency),
        )

        try:
            return self._dispatch_dependency(dependency, context)
        except Exception as e:
            self._logger.error(
                "Dependency execution failed: %s - %s", type(dependency).__name__, str(e)
            )
            raise

    def set_agent_registry(self, registry: Any) -> None:
        """Set the agent registry for agent dependency resolution.

        Args:
            registry: Agent registry to use
        """
        self._agent_execution_service.set_agent_registry(registry)

    def set_interrupt_service(self, service: InterruptService) -> None:
        """Set the interrupt service.

        Args:
            service: Interrupt service to use
        """
        self._interrupt_service = service

    # MARK: - Dependency Dispatch

    def _dispatch_dependency(
        self,
        dependency: BaseDependency,
        context: ExecutionContext,
    ) -> Any:
        """Dispatch dependency to appropriate handler.

        Args:
            dependency: Dependency to dispatch
            context: Execution context

        Returns:
            Result of dependency execution

        Raises:
            ValueError: If dependency type is not supported
        """
        if isinstance(dependency, AgentDependency):
            return self._execute_agent_dependency(dependency, context)
        if isinstance(dependency, InterruptDependency):
            return self._execute_interrupt_dependency(dependency, context)
        if isinstance(dependency, DataDependency):
            return self._execute_data_dependency(dependency, context)
        if isinstance(dependency, ToolDependency):
            return self._execute_tool_dependency(dependency, context)

        raise ValueError(f"Unsupported dependency type: {type(dependency)}")

    # MARK: - Agent Dependencies

    def _execute_agent_dependency(
        self,
        dependency: AgentDependency,
        context: ExecutionContext,
    ) -> Any:
        """Execute an agent dependency.

        Args:
            dependency: Agent dependency to execute
            context: Execution context

        Returns:
            Result from agent execution
        """
        messages = self._get_context_messages(context)
        return self._agent_execution_service.execute_agent_dependency(
            dependency, messages, context.timeout
        )

    def _get_context_messages(self, context: ExecutionContext) -> Sequence[BaseMessage]:
        """Get messages from context, creating default if none provided.

        Args:
            context: Execution context

        Returns:
            Sequence of messages
        """
        if context.messages:
            return cast(Sequence[BaseMessage], context.messages)

        from maivn_shared import HumanMessage

        return [HumanMessage(content="Execute task based on dependency context")]

    # MARK: - Interrupt Dependencies

    def _execute_interrupt_dependency(
        self,
        dependency: InterruptDependency,
        context: ExecutionContext,
    ) -> Any:
        """Execute an interrupt dependency.

        Args:
            dependency: Interrupt dependency to execute
            context: Execution context

        Returns:
            User input result
        """
        prompt = getattr(dependency, "prompt", "Please provide input:")
        input_type = getattr(dependency, "input_type", "text")
        choices = getattr(dependency, "choices", [])

        input_handler = getattr(dependency, "input_handler", None)
        if input_handler and callable(input_handler):
            result = self._try_custom_input_handler(
                input_handler, prompt, input_type=input_type, choices=choices
            )
            if result is not None:
                return result

        if input_type == "choice" and choices:
            return self._interrupt_service.get_user_choice(prompt, list(choices))
        if input_type == "boolean":
            return self._interrupt_service.get_user_confirmation(prompt)
        return self._interrupt_service.get_user_input(prompt)

    def _try_custom_input_handler(
        self,
        handler: Any,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
    ) -> Any | None:
        """Try to execute custom input handler.

        Args:
            handler: Custom input handler
            prompt: Input prompt
            input_type: Type of input (text, choice, boolean, etc.)
            choices: Available choices for choice input types

        Returns:
            Handler result or None if failed
        """
        try:
            # Try calling with extended signature first (input_type, choices)
            import inspect

            sig = inspect.signature(handler)
            params = list(sig.parameters.keys())

            if "input_type" in params or "choices" in params:
                # Handler supports extended signature
                kwargs: dict[str, Any] = {}
                if "input_type" in params:
                    kwargs["input_type"] = input_type
                if "choices" in params:
                    kwargs["choices"] = choices or []
                try:
                    return handler(prompt, **kwargs)
                except TypeError as exc:
                    message = str(exc)
                    if "unexpected keyword" in message or "unexpected keyword argument" in message:
                        return handler(prompt)
                    raise

            # Fall back to basic signature
            return handler(prompt)
        except Exception as e:
            self._logger.warning("Custom input handler failed, falling back to default: %s", e)
            return None

    # MARK: - Data Dependencies

    def _execute_data_dependency(
        self,
        dependency: DataDependency,
        context: ExecutionContext,
    ) -> Any:
        """Execute a data dependency.

        Args:
            dependency: Data dependency to execute
            context: Execution context

        Returns:
            Data value from scope

        Raises:
            ValueError: If data_key is missing or not found in scope
        """
        data_key = getattr(dependency, "data_key", None)
        if not data_key:
            raise ValueError("DataDependency must have a data_key")

        private_data = self._get_private_data_from_context(context)

        if data_key not in private_data:
            raise ValueError(f"Data key '{data_key}' not found in scope private_data")

        return private_data[data_key]

    def _get_private_data_from_context(self, context: ExecutionContext) -> dict[str, Any]:
        """Get private_data from context scope.

        Args:
            context: Execution context

        Returns:
            Private data dictionary

        Raises:
            ValueError: If no scope available
        """
        if not context.scope:
            raise ValueError("No scope available in context for data dependency")

        return getattr(context.scope, "private_data", {})

    # MARK: - Tool Dependencies

    def _execute_tool_dependency(
        self,
        dependency: ToolDependency,
        context: ExecutionContext,
    ) -> Any:
        """Execute a tool dependency.

        Args:
            dependency: Tool dependency to execute
            context: Execution context

        Returns:
            Result from tool execution

        Raises:
            ValueError: Tool dependencies should be handled by tool execution service
        """
        raise ValueError(
            "Tool dependencies should be resolved by tool execution service, "
            "not dependency execution service"
        )


__all__ = [
    "DependencyExecutionService",
]
