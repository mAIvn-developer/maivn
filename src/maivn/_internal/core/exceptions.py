"""Custom exception hierarchy for the maivn SDK.

This module defines SDK-specific exceptions built on the shared MaivnError base.
All exceptions inherit from maivn_shared.MaivnError for consistent error handling.
"""

from __future__ import annotations

from typing import Any

from maivn_shared import ConfigurationError as SharedConfigurationError
from maivn_shared import MaivnError
from maivn_shared import SerializationError as SharedSerializationError

# MARK: Tool Execution Errors


class ToolExecutionError(MaivnError):
    """Raised when tool execution fails."""

    def __init__(
        self,
        tool_id: str,
        reason: str,
        original_error: Exception | None = None,
    ) -> None:
        self.tool_id = tool_id
        self.reason = reason
        self.original_error = original_error
        super().__init__(f"Tool '{tool_id}' execution failed: {reason}")


# MARK: Authentication/Server Errors


class ServerAuthenticationError(MaivnError):
    """Raised when maivn-server rejects a request due to missing/invalid authentication."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        server_error: str | None = None,
        server_message: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.server_error = server_error
        self.server_message = server_message
        self.hint = hint
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        base = f"maivn-server authentication failed ({self.status_code}) for {self.url}"
        detail_parts: list[str] = []
        if self.server_error:
            detail_parts.append(self.server_error)
        if self.server_message:
            detail_parts.append(self.server_message)
        if detail_parts:
            base += f": {' - '.join(detail_parts)}"
        if self.hint:
            base += f"\n\nHint: {self.hint}"
        return base


class ToolNotFoundError(MaivnError):
    """Raised when a requested tool cannot be found."""

    def __init__(
        self,
        tool_id: str,
        available_tools: list[str] | None = None,
    ) -> None:
        self.tool_id = tool_id
        self.available_tools = available_tools or []
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        message = f"Tool '{self.tool_id}' not found"
        if self.available_tools:
            tools_preview = ", ".join(self.available_tools[:10])
            message += f". Available tools: {tools_preview}"
            if len(self.available_tools) > 10:
                message += f" (and {len(self.available_tools) - 10} more)"
        return message


class ArgumentValidationError(MaivnError):
    """Raised when tool arguments fail validation."""

    def __init__(
        self,
        tool_name: str,
        expected_params: list[str] | None = None,
        provided_params: list[str] | None = None,
        details: str | None = None,
    ) -> None:
        self.tool_name = tool_name
        self.expected_params = expected_params or []
        self.provided_params = provided_params or []
        self.details = details
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        message = f"Invalid arguments for tool '{self.tool_name}'"

        if self.expected_params and self.provided_params:
            missing = set(self.expected_params) - set(self.provided_params)
            unexpected = set(self.provided_params) - set(self.expected_params)
            if missing:
                message += f". Missing: {', '.join(missing)}"
            if unexpected:
                message += f". Unexpected: {', '.join(unexpected)}"

        if self.details:
            message += f". {self.details}"

        return message


# MARK: Dependency Resolution Errors


class DependencyResolutionError(MaivnError):
    """Raised when dependency resolution fails."""

    def __init__(
        self,
        dependency_type: str,
        dependency_name: str,
        details: str,
    ) -> None:
        self.dependency_type = dependency_type
        self.dependency_name = dependency_name
        self.details = details
        super().__init__(f"Failed to resolve {dependency_type} '{dependency_name}': {details}")


class AgentNotFoundError(DependencyResolutionError):
    """Raised when a dependent agent cannot be found."""

    def __init__(
        self,
        agent_id: str,
        available_agents: list[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.available_agents = available_agents or []
        super().__init__(
            dependency_type="AgentDependency",
            dependency_name=agent_id,
            details=self._build_details(),
        )

    def _build_details(self) -> str:
        details = "Agent not found in swarm"
        if self.available_agents:
            details += f". Available: {', '.join(self.available_agents)}"
        return details


class ToolDependencyNotFoundError(DependencyResolutionError):
    """Raised when a tool dependency result is not found in context."""

    def __init__(
        self,
        tool_id: str,
        available_results: list[str] | None = None,
    ) -> None:
        self.tool_id = tool_id
        self.available_results = available_results or []
        super().__init__(
            dependency_type="ToolDependency",
            dependency_name=tool_id,
            details=self._build_details(),
        )

    def _build_details(self) -> str:
        details = "Tool result not found in context"
        if self.available_results:
            details += f". Available: {', '.join(self.available_results)}"
        return details


# MARK: State Compilation Errors


class StateCompilationError(MaivnError):
    """Raised when state compilation fails."""

    def __init__(
        self,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.reason = reason
        self.context = context or {}
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        message = f"State compilation failed: {self.reason}"
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            message += f" ({context_str})"
        return message


class DynamicToolCreationError(StateCompilationError):
    """Raised when dynamic tool creation fails."""

    def __init__(
        self,
        tool_type: str,
        target_id: str,
        reason: str,
    ) -> None:
        self.tool_type = tool_type
        self.target_id = target_id
        super().__init__(
            reason=f"Failed to create {tool_type} tool for '{target_id}': {reason}",
            context={"tool_type": tool_type, "target_id": target_id},
        )


# MARK: Configuration Errors


class ConfigurationError(SharedConfigurationError):
    """Raised when there's a configuration problem in the SDK."""

    def __init__(
        self,
        setting: str,
        issue: str,
        suggestion: str | None = None,
    ) -> None:
        self.setting = setting
        self.issue = issue
        self.suggestion = suggestion

        message = f"Configuration error for '{setting}': {issue}"
        if suggestion:
            message += f". {suggestion}"

        super().__init__(
            message,
            setting=setting,
            expected=None,
            actual=None,
            suggestion=suggestion,
        )


class SwarmContextError(MaivnError):
    """Raised when agent dependencies are used outside a Swarm context."""

    def __init__(self, agent_id: str | None = None) -> None:
        self.agent_id = agent_id
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        if self.agent_id:
            message = f"Agent '{self.agent_id}' has agent dependencies but is not part of a Swarm"
        else:
            message = (
                "Agent dependencies (depends_on_agent) require the agent to be part of a Swarm"
            )
        return f"{message}. Create a Swarm and add the agent to enable cross-agent communication."


# MARK: Serialization Errors


class SerializationError(SharedSerializationError):
    """Raised when serialization/deserialization fails in the SDK."""

    def __init__(
        self,
        data_type: str,
        operation: str,
        reason: str,
    ) -> None:
        self.reason = reason
        super().__init__(
            f"Failed to {operation} {data_type}: {reason}",
            data_type=data_type,
            operation=operation,
        )


class PydanticDeserializationError(SerializationError):
    """Raised when Pydantic model deserialization fails."""

    def __init__(
        self,
        model_name: str,
        reason: str,
        field_name: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.field_name = field_name
        super().__init__(
            data_type="Pydantic model",
            operation="deserialize",
            reason=self._build_reason(reason),
        )

    def _build_reason(self, reason: str) -> str:
        detailed = f"Model '{self.model_name}'"
        if self.field_name:
            detailed += f", field '{self.field_name}'"
        return f"{detailed}: {reason}"


__all__ = [
    # Base
    "MaivnError",
    # Server/auth
    "ServerAuthenticationError",
    # Tool execution
    "ArgumentValidationError",
    "ToolExecutionError",
    "ToolNotFoundError",
    # Dependencies
    "AgentNotFoundError",
    "DependencyResolutionError",
    "ToolDependencyNotFoundError",
    # State compilation
    "DynamicToolCreationError",
    "StateCompilationError",
    # Configuration
    "ConfigurationError",
    "SwarmContextError",
    # Serialization
    "PydanticDeserializationError",
    "SerializationError",
]
