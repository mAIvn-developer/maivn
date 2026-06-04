# pyright: strict
"""Custom exception hierarchy for the maivn SDK.

This module defines SDK-specific exceptions built on the shared MaivnError base.
All exceptions inherit from maivn_shared.MaivnError for consistent error handling.
"""

from __future__ import annotations

from maivn_shared import ConfigurationError as SharedConfigurationError
from maivn_shared import MaivnError
from maivn_shared import SerializationError as SharedSerializationError

# MARK: Configuration

AVAILABLE_TOOLS_PREVIEW_LIMIT = 10


# MARK: Message Helpers


def _join_message_parts(base: str, parts: list[str], *, separator: str = ". ") -> str:
    """Append present message parts to a base message with the requested separator."""
    present_parts = [part for part in parts if part]
    if not present_parts:
        return base
    return f"{base}{separator}{separator.join(present_parts)}"


# MARK: Tool Execution Errors


class ToolExecutionError(MaivnError):
    """Raised when tool execution fails."""

    def __init__(
        self,
        tool_id: str,
        reason: str,
        original_error: Exception | None = None,
    ) -> None:
        self.tool_id: str = tool_id
        self.reason: str = reason
        self.original_error: Exception | None = original_error
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
        self.status_code: int = status_code
        self.url: str = url
        self.server_error: str | None = server_error
        self.server_message: str | None = server_message
        self.hint: str | None = hint
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        base = f"maivn-server authentication failed ({self.status_code}) for {self.url}"
        detail_parts: list[str] = []
        if self.server_error:
            detail_parts.append(self.server_error)
        if self.server_message:
            detail_parts.append(self.server_message)
        message = _join_message_parts(base, [" - ".join(detail_parts)], separator=": ")
        if self.hint:
            message = _join_message_parts(message, [f"Hint: {self.hint}"], separator="\n\n")
        return message


class ToolNotFoundError(MaivnError):
    """Raised when a requested tool cannot be found."""

    def __init__(
        self,
        tool_id: str,
        available_tools: list[str] | None = None,
    ) -> None:
        self.tool_id: str = tool_id
        self.available_tools: list[str] = available_tools or []
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        message = f"Tool '{self.tool_id}' not found"
        if self.available_tools:
            tools_preview = ", ".join(self.available_tools[:AVAILABLE_TOOLS_PREVIEW_LIMIT])
            remaining_count = len(self.available_tools) - AVAILABLE_TOOLS_PREVIEW_LIMIT
            suffix = f" (and {remaining_count} more)" if remaining_count > 0 else ""
            message = _join_message_parts(message, [f"Available tools: {tools_preview}{suffix}"])
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
        self.tool_name: str = tool_name
        self.expected_params: list[str] = expected_params or []
        self.provided_params: list[str] = provided_params or []
        self.details: str | None = details
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        detail_parts: list[str] = []

        if self.expected_params and self.provided_params:
            missing = set(self.expected_params) - set(self.provided_params)
            unexpected = set(self.provided_params) - set(self.expected_params)
            if missing:
                detail_parts.append(f"Missing: {', '.join(missing)}")
            if unexpected:
                detail_parts.append(f"Unexpected: {', '.join(unexpected)}")

        if self.details:
            detail_parts.append(self.details)

        return _join_message_parts(f"Invalid arguments for tool '{self.tool_name}'", detail_parts)


# MARK: Dependency Resolution Errors


class DependencyResolutionError(MaivnError):
    """Raised when dependency resolution fails."""

    def __init__(
        self,
        dependency_type: str,
        dependency_name: str,
        details: str,
    ) -> None:
        self.dependency_type: str = dependency_type
        self.dependency_name: str = dependency_name
        self.details: str = details
        super().__init__(f"Failed to resolve {dependency_type} '{dependency_name}': {details}")


class AgentNotFoundError(DependencyResolutionError):
    """Raised when a dependent agent cannot be found."""

    def __init__(
        self,
        agent_id: str,
        available_agents: list[str] | None = None,
    ) -> None:
        self.agent_id: str = agent_id
        self.available_agents: list[str] = available_agents or []
        super().__init__(
            dependency_type="AgentDependency",
            dependency_name=agent_id,
            details=self._build_details(),
        )

    def _build_details(self) -> str:
        detail_parts: list[str] = []
        if self.available_agents:
            detail_parts.append(f"Available: {', '.join(self.available_agents)}")
        return _join_message_parts("Agent not found in swarm", detail_parts)


class ToolDependencyNotFoundError(DependencyResolutionError):
    """Raised when a tool dependency result is not found in context."""

    def __init__(
        self,
        tool_id: str,
        available_results: list[str] | None = None,
    ) -> None:
        self.tool_id: str = tool_id
        self.available_results: list[str] = available_results or []
        super().__init__(
            dependency_type="ToolDependency",
            dependency_name=tool_id,
            details=self._build_details(),
        )

    def _build_details(self) -> str:
        detail_parts: list[str] = []
        if self.available_results:
            detail_parts.append(f"Available: {', '.join(self.available_results)}")
        return _join_message_parts("Tool result not found in context", detail_parts)


# MARK: State Compilation Errors


class StateCompilationError(MaivnError):
    """Raised when state compilation fails."""

    def __init__(
        self,
        reason: str,
        context: dict[str, object] | None = None,
    ) -> None:
        self.reason: str = reason
        self.context: dict[str, object] = context or {}
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
        self.tool_type: str = tool_type
        self.target_id: str = target_id
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
        self.setting: str = setting
        self.issue: str = issue
        self.suggestion: str | None = suggestion

        message = _join_message_parts(
            f"Configuration error for '{setting}': {issue}",
            [suggestion] if suggestion else [],
        )

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
        self.agent_id: str | None = agent_id
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
        self.reason: str = reason
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
        self.model_name: str = model_name
        self.field_name: str | None = field_name
        super().__init__(
            data_type="Pydantic model",
            operation="deserialize",
            reason=self._build_reason(reason),
        )

    def _build_reason(self, reason: str) -> str:
        detail_parts: list[str] = []
        if self.field_name:
            detail_parts.append(f"field '{self.field_name}'")
        detailed = _join_message_parts(f"Model '{self.model_name}'", detail_parts, separator=", ")
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
