"""Agent execution service for handling agent dependency invocations.

This service manages the execution of other agents when depends_on_agent
dependencies are encountered during tool execution.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from typing import Any

from maivn_shared import AgentDependency, BaseMessage, SessionResponse

from maivn._internal.core.utils.logger import ensure_domain_logger
from maivn._internal.utils.reporting.context import get_current_reporter

# MARK: - AgentExecutionService


class AgentExecutionService:
    """Service for executing agent dependencies."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        logger: None = None,
        agent_registry: Any | None = None,
    ) -> None:
        """Initialize agent execution service.

        Args:
            logger: Optional logger for tracking agent executions
            agent_registry: Registry to resolve agent references
        """
        self._logger = ensure_domain_logger(logger)
        self._agent_registry = agent_registry

    # MARK: - Public Methods

    def execute_agent_dependency(
        self,
        dependency: AgentDependency,
        context_messages: Sequence[BaseMessage],
        timeout: float | None = None,
    ) -> Any:
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
        self._logger.info(
            "Executing agent dependency: %s (arg: %s)",
            dependency.agent_id,
            dependency.arg_name,
        )

        agent = self._resolve_agent(dependency.agent_id)
        if not agent:
            raise ValueError(f"Cannot resolve agent dependency: {dependency.agent_id}")

        return self._invoke_agent(agent, dependency.agent_id, context_messages)

    def set_agent_registry(self, registry: Any) -> None:
        """Set the agent registry for dependency resolution.

        Args:
            registry: Agent registry to use
        """
        self._agent_registry = registry

    # MARK: - Agent Resolution

    def _resolve_agent(self, agent_id: str) -> Any:
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

    def _resolve_by_get_agent(self, agent_id: str) -> Any:
        """Try to resolve agent using get_agent method."""
        if self._agent_registry is not None and hasattr(self._agent_registry, "get_agent"):
            return self._agent_registry.get_agent(agent_id)
        return None

    def _resolve_by_name(self, agent_id: str) -> Any:
        """Try to resolve agent using get_agent_by_name method."""
        if self._agent_registry is not None and hasattr(self._agent_registry, "get_agent_by_name"):
            return self._agent_registry.get_agent_by_name(agent_id)
        return None

    def _resolve_from_agents_list(self, agent_id: str) -> Any:
        """Try to resolve agent from agents list (swarm)."""
        if self._agent_registry is None or not hasattr(self._agent_registry, "agents"):
            return None

        for agent in self._agent_registry.agents:
            if self._agent_matches_id(agent, agent_id):
                return agent
        return None

    def _agent_matches_id(self, agent: Any, agent_id: str) -> bool:
        """Check if agent matches the given ID or name."""
        return getattr(agent, "id", None) == agent_id or getattr(agent, "name", None) == agent_id

    # MARK: - Agent Invocation

    def _invoke_agent(
        self,
        agent: Any,
        agent_id: str,
        context_messages: Sequence[BaseMessage],
    ) -> Any:
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

        except Exception as e:
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
    def _resolve_agent_name(agent: Any, fallback_agent_id: str) -> str:
        name = getattr(agent, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return fallback_agent_id

    @staticmethod
    def _resolve_target_agent_id(agent: Any, fallback_agent_id: str) -> str:
        agent_identifier = getattr(agent, "id", None)
        if isinstance(agent_identifier, str) and agent_identifier.strip():
            return agent_identifier.strip()
        return fallback_agent_id

    @staticmethod
    def _resolve_swarm_name(agent: Any) -> str | None:
        get_swarm = getattr(agent, "get_swarm", None)
        swarm = get_swarm() if callable(get_swarm) else getattr(agent, "_swarm", None)
        if swarm is None:
            return None
        swarm_name = getattr(swarm, "name", None)
        if isinstance(swarm_name, str) and swarm_name.strip():
            return swarm_name.strip()
        return None

    # MARK: - Result Extraction

    def _extract_agent_result(self, response: SessionResponse) -> Any:
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

    def _extract_from_result(self, response: SessionResponse) -> Any:
        """Try to extract result from response.result attribute."""
        if hasattr(response, "result") and response.result:
            return response.result
        return None

    def _extract_from_metadata(self, response: SessionResponse) -> Any:
        """Try to extract result from response metadata."""
        if hasattr(response, "metadata") and response.metadata:
            return response.metadata.get("result")
        return None

    def _extract_from_messages(self, response: SessionResponse) -> Any:
        """Try to extract result from last message content."""
        if hasattr(response, "messages") and response.messages:
            last_message = response.messages[-1]
            if hasattr(last_message, "content"):
                return last_message.content
        return None

    def _extract_fallback(self, response: SessionResponse) -> Any:
        """Fallback extraction using model_dump or empty dict."""
        if hasattr(response, "model_dump"):
            return response.model_dump()
        return {}


# MARK: - MockAgentExecutionService


class MockAgentExecutionService(AgentExecutionService):
    """Mock agent execution service for testing."""

    def __init__(self, mock_responses: dict[str, Any] | None = None) -> None:
        """Initialize mock service.

        Args:
            mock_responses: Dictionary mapping agent_id to mock response
        """
        super().__init__()
        self._mock_responses = mock_responses or {}

    def execute_agent_dependency(
        self,
        dependency: AgentDependency,
        context_messages: Sequence[BaseMessage],
        timeout: float | None = None,
    ) -> Any:
        """Return mock response for agent dependency.

        Args:
            dependency: Agent dependency
            context_messages: Context messages (ignored in mock)
            timeout: Timeout (ignored in mock)

        Returns:
            Mock response for the agent
        """
        if dependency.agent_id in self._mock_responses:
            return self._mock_responses[dependency.agent_id]

        return f"mock_response_for_{dependency.agent_id}"

    def add_mock_response(self, agent_id: str, response: Any) -> None:
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
