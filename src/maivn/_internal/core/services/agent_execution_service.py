"""Agent execution service for handling agent dependency invocations.

This service manages the execution of other agents when depends_on_agent
dependencies are encountered during tool execution.
"""

# pyright: strict
from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Protocol, TypeAlias, cast, runtime_checkable

from maivn_shared import AgentDependency, BaseMessage
from maivn_shared.infrastructure.logging import LoggerProtocol, get_optional_logger
from typing_extensions import override

from maivn._internal.utils.reporting.context import get_current_reporter

# MARK: - Types

AgentExecutionResult: TypeAlias = object


@runtime_checkable
class AgentExecutionTarget(Protocol):
    """Agent surface required for dependency invocation."""

    id: str
    name: str | None

    def invoke(self, messages: Sequence[BaseMessage]) -> object:
        """Invoke the agent and return its raw response."""
        ...


@runtime_checkable
class AgentLookupRegistry(Protocol):
    """Registry surface for direct agent lookup."""

    def get_agent(self, agent_id: str) -> AgentExecutionTarget | None:
        """Get an agent by ID."""
        ...

    def get_agent_by_name(self, name: str) -> AgentExecutionTarget | None:
        """Get an agent by name."""
        ...


@runtime_checkable
class AgentSequenceRegistry(Protocol):
    """Registry surface for swarm-scoped agent lists."""

    agents: Sequence[AgentExecutionTarget]


AgentRegistry: TypeAlias = AgentLookupRegistry | AgentSequenceRegistry


@runtime_checkable
class AgentWithSwarmMethod(Protocol):
    """Agent surface exposing a swarm accessor."""

    def get_swarm(self) -> object | None:
        """Get parent swarm."""
        ...


@runtime_checkable
class NamedObject(Protocol):
    """Object carrying an optional display name."""

    name: str | None


class MessageWithContent(Protocol):
    """Message-like object carrying content."""

    content: object


@runtime_checkable
class ResponseWithResult(Protocol):
    """Response-like object with a result field."""

    result: object | None


@runtime_checkable
class ResponseWithMetadata(Protocol):
    """Response-like object with metadata."""

    metadata: Mapping[str, object] | None


@runtime_checkable
class ResponseWithMessages(Protocol):
    """Response-like object with messages."""

    messages: Sequence[MessageWithContent] | None


@runtime_checkable
class DumpableResponse(Protocol):
    """Response-like object that can be serialized as a fallback."""

    def model_dump(self) -> Mapping[str, object]:
        """Dump response fields."""
        ...


# MARK: - AgentExecutionService


class AgentExecutionService:
    """Service for executing agent dependencies."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        logger: LoggerProtocol | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        """Initialize agent execution service.

        Args:
            logger: Optional logger for tracking agent executions
            agent_registry: Registry to resolve agent references
        """
        self._logger: LoggerProtocol = logger or get_optional_logger()
        self._agent_registry: AgentRegistry | None = agent_registry

    # MARK: - Public Methods

    def execute_agent_dependency(
        self,
        dependency: AgentDependency,
        context_messages: Sequence[BaseMessage],
        timeout: float | None = None,
    ) -> AgentExecutionResult:
        """Execute an agent dependency by invoking the referenced agent.

        Args:
            dependency: Agent dependency to execute
            context_messages: Messages to pass to the agent
            timeout: Optional timeout for agent execution

        Returns:
            Result from agent execution

        Raises:
            ValueError: If agent cannot be resolved or executed
        """
        _ = timeout
        self._logger.info(
            "Executing agent dependency: %s (arg: %s)",
            dependency.agent_id,
            dependency.arg_name,
        )

        agent = self._resolve_agent(dependency.agent_id)
        if not agent:
            raise ValueError(f"Cannot resolve agent dependency: {dependency.agent_id}")

        return self._invoke_agent(agent, dependency.agent_id, context_messages)

    def set_agent_registry(self, registry: AgentRegistry) -> None:
        """Set the agent registry for dependency resolution.

        Args:
            registry: Agent registry to use
        """
        self._agent_registry = registry

    # MARK: - Agent Resolution

    def _resolve_agent(self, agent_id: str) -> AgentExecutionTarget | None:
        """Resolve an agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            Resolved agent instance or None

        Raises:
            ValueError: If no agent registry is configured
        """
        if not self._agent_registry:
            raise ValueError("No agent registry configured for dependency resolution")

        return (
            self._resolve_by_get_agent(agent_id)
            or self._resolve_by_name(agent_id)
            or self._resolve_from_agents_list(agent_id)
        )

    def _resolve_by_get_agent(self, agent_id: str) -> AgentExecutionTarget | None:
        """Try to resolve agent using get_agent method."""
        registry = self._agent_registry
        if isinstance(registry, AgentLookupRegistry):
            return registry.get_agent(agent_id)
        return None

    def _resolve_by_name(self, agent_id: str) -> AgentExecutionTarget | None:
        """Try to resolve agent using get_agent_by_name method."""
        registry = self._agent_registry
        if isinstance(registry, AgentLookupRegistry):
            return registry.get_agent_by_name(agent_id)
        return None

    def _resolve_from_agents_list(self, agent_id: str) -> AgentExecutionTarget | None:
        """Try to resolve agent from agents list (swarm)."""
        registry = self._agent_registry
        if not isinstance(registry, AgentSequenceRegistry):
            return None

        for agent in registry.agents:
            if self._agent_matches_id(agent, agent_id):
                return agent
        return None

    def _agent_matches_id(self, agent: AgentExecutionTarget, agent_id: str) -> bool:
        """Check if agent matches the given ID or name."""
        return agent.id == agent_id or agent.name == agent_id

    # MARK: - Agent Invocation

    def _invoke_agent(
        self,
        agent: AgentExecutionTarget,
        agent_id: str,
        context_messages: Sequence[BaseMessage],
    ) -> AgentExecutionResult:
        """Invoke an agent and return the result.

        Args:
            agent: Agent instance to invoke
            agent_id: Agent identifier for logging
            context_messages: Messages to pass to the agent

        Returns:
            Extracted result from agent response

        Raises:
            ValueError: If agent execution fails
        """
        reporter = get_current_reporter()
        tool_name = self._resolve_agent_name(agent, agent_id)
        target_agent_id = self._resolve_target_agent_id(agent, agent_id)
        swarm_name = self._resolve_swarm_name(agent)
        event_id = f"agent_dependency_{uuid.uuid4()}"

        if reporter is not None:
            reporter.report_tool_start(
                tool_name,
                event_id,
                "agent",
                tool_name,
                {"agent_id": target_agent_id},
                swarm_name,
            )

        start_time: float | None = None
        try:
            start_time = time.perf_counter()
            response = agent.invoke(messages=context_messages)
            elapsed_time = time.perf_counter() - start_time
            extracted_result = self._extract_agent_result(response)

            if reporter is not None:
                reporter.report_tool_complete(
                    event_id,
                    elapsed_ms=int(elapsed_time * 1000.0),
                    result=extracted_result,
                )

            self._logger.info(
                "Agent dependency completed: %s (elapsed: %.2fs)",
                agent_id,
                elapsed_time,
            )

            return extracted_result

        except Exception as e:  # noqa: BLE001 - agent.invoke() third-party; failure surfaced as ValueError
            elapsed_time = time.perf_counter() - start_time if start_time is not None else 0.0
            if reporter is not None:
                reporter.report_tool_error(
                    tool_name,
                    str(e),
                    event_id=event_id,
                    elapsed_ms=int(elapsed_time * 1000.0),
                )
            self._logger.error("Agent dependency failed: %s - %s", agent_id, str(e))
            raise ValueError(f"Agent execution failed: {e}") from e

    @staticmethod
    def _resolve_agent_name(agent: AgentExecutionTarget, fallback_agent_id: str) -> str:
        name = agent.name
        if isinstance(name, str) and name.strip():
            return name.strip()
        return fallback_agent_id

    @staticmethod
    def _resolve_target_agent_id(agent: AgentExecutionTarget, fallback_agent_id: str) -> str:
        agent_identifier = agent.id
        if agent_identifier.strip():
            return agent_identifier.strip()
        return fallback_agent_id

    @staticmethod
    def _resolve_swarm_name(agent: AgentExecutionTarget) -> str | None:
        swarm: object | None = None
        if isinstance(agent, AgentWithSwarmMethod):
            swarm = agent.get_swarm()
        if swarm is None:
            return None
        if not isinstance(swarm, NamedObject):
            return None
        swarm_name = swarm.name
        if isinstance(swarm_name, str) and swarm_name.strip():
            return swarm_name.strip()
        return None

    # MARK: - Result Extraction

    def _extract_agent_result(self, response: object) -> AgentExecutionResult:
        """Extract the meaningful result from an agent response.

        Args:
            response: Agent session response

        Returns:
            Extracted result data
        """
        return (
            self._extract_from_result(response)
            or self._extract_from_metadata(response)
            or self._extract_from_messages(response)
            or self._extract_fallback(response)
        )

    def _extract_from_result(self, response: object) -> object | None:
        """Try to extract result from response.result attribute."""
        if isinstance(response, ResponseWithResult) and response.result:
            return response.result
        return None

    def _extract_from_metadata(self, response: object) -> object | None:
        """Try to extract result from response metadata."""
        if isinstance(response, ResponseWithMetadata) and response.metadata:
            return response.metadata.get("result")
        return None

    def _extract_from_messages(self, response: object) -> object | None:
        """Try to extract result from last message content."""
        if isinstance(response, ResponseWithMessages) and response.messages:
            last_message = response.messages[-1]
            return last_message.content
        return None

    def _extract_fallback(self, response: object) -> object:
        """Fallback extraction using model_dump or empty dict."""
        if isinstance(response, DumpableResponse):
            return cast(object, response.model_dump())
        return {}


# MARK: - MockAgentExecutionService


class MockAgentExecutionService(AgentExecutionService):
    """Mock agent execution service for testing."""

    def __init__(self, mock_responses: dict[str, object] | None = None) -> None:
        """Initialize mock service.

        Args:
            mock_responses: Dictionary mapping agent_id to mock response
        """
        super().__init__()
        self._mock_responses: dict[str, object] = mock_responses or {}

    @override
    def execute_agent_dependency(
        self,
        dependency: AgentDependency,
        context_messages: Sequence[BaseMessage],
        timeout: float | None = None,
    ) -> AgentExecutionResult:
        """Return mock response for agent dependency.

        Args:
            dependency: Agent dependency
            context_messages: Context messages (ignored in mock)
            timeout: Timeout (ignored in mock)

        Returns:
            Mock response for the agent
        """
        _ = (context_messages, timeout)
        if dependency.agent_id in self._mock_responses:
            return self._mock_responses[dependency.agent_id]

        return f"mock_response_for_{dependency.agent_id}"

    def add_mock_response(self, agent_id: str, response: object) -> None:
        """Add a mock response for an agent.

        Args:
            agent_id: Agent identifier
            response: Mock response to return
        """
        self._mock_responses[agent_id] = response


__all__ = [
    "AgentExecutionService",
    "MockAgentExecutionService",
]
