"""Builder for constructing agent orchestrators.
Provides a fluent API for dependency wiring and configuration overrides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol

from maivn._internal.core.interfaces import AgentOrchestratorInterface
from maivn._internal.utils.logging import get_optional_logger

if TYPE_CHECKING:
    from maivn._internal.core.services import (
        BackgroundExecutor,
        EventStreamProcessor,
        SessionService,
        StateCompiler,
        ToolExecutionOrchestrator,
        ToolExecutionService,
    )
    from maivn._internal.core.services.interrupt_service import InterruptService
    from maivn._internal.core.tool_specs import ToolSpecFactory


class OrchestratorBuilder:
    """Builder for constructing agent orchestrators with dependency injection.

    Provides a fluent interface for configuring orchestrator dependencies
    and creating orchestrator instances with proper defaults.
    """

    # MARK: - Initialization

    def __init__(self) -> None:
        """Initialize the builder with empty configuration."""
        self._agent: Any | None = None
        self._client: SessionClientProtocol | None = None
        self._logger: MetricsLoggerProtocol | None = None
        self._timeout: float | None = None
        self._pending_event_timeout: float | None = None
        self._tool_spec_factory: ToolSpecFactory | None = None
        self._state_compiler: StateCompiler | None = None
        self._tool_execution_service: ToolExecutionService | None = None
        self._tool_execution_orchestrator: ToolExecutionOrchestrator | None = None
        self._event_stream_processor: EventStreamProcessor | None = None
        self._session_service: SessionService | None = None
        self._background_executor: BackgroundExecutor | None = None
        self._interrupt_service: InterruptService | None = None

    # MARK: - Core Configuration

    def with_agent(self, agent: Any) -> OrchestratorBuilder:
        """Configure the agent to orchestrate."""
        self._agent = agent
        return self

    def with_client(self, client: SessionClientProtocol) -> OrchestratorBuilder:
        """Configure the session client."""
        self._client = client
        return self

    def with_logger(self, logger: MetricsLoggerProtocol) -> OrchestratorBuilder:
        """Configure the logger."""
        self._logger = logger
        return self

    def with_timeout(self, timeout: float) -> OrchestratorBuilder:
        """Override the HTTP timeout used for client calls."""
        self._timeout = timeout
        return self

    def with_pending_event_timeout(self, timeout: float) -> OrchestratorBuilder:
        """Override the pending tool-event timeout for SSE processing."""
        self._pending_event_timeout = timeout
        return self

    # MARK: - Service Configuration

    def with_tool_spec_factory(self, factory: ToolSpecFactory) -> OrchestratorBuilder:
        """Override the tool spec factory used during state compilation."""
        self._tool_spec_factory = factory
        return self

    def with_state_compiler(self, compiler: StateCompiler) -> OrchestratorBuilder:
        """Provide a custom StateCompiler implementation."""
        self._state_compiler = compiler
        return self

    def with_tool_execution_service(
        self,
        service: ToolExecutionService,
    ) -> OrchestratorBuilder:
        """Provide a custom ToolExecutionService implementation."""
        self._tool_execution_service = service
        return self

    def with_tool_execution_orchestrator(
        self,
        orchestrator: ToolExecutionOrchestrator,
    ) -> OrchestratorBuilder:
        """Provide a custom ToolExecutionOrchestrator implementation."""
        self._tool_execution_orchestrator = orchestrator
        return self

    def with_event_stream_processor(
        self,
        processor: EventStreamProcessor,
    ) -> OrchestratorBuilder:
        """Provide a custom EventStreamProcessor implementation."""
        self._event_stream_processor = processor
        return self

    def with_session_service(self, session_service: SessionService) -> OrchestratorBuilder:
        """Provide a custom SessionService implementation."""
        self._session_service = session_service
        return self

    def with_background_executor(self, executor: BackgroundExecutor) -> OrchestratorBuilder:
        """Provide a custom BackgroundExecutor implementation."""
        self._background_executor = executor
        return self

    def with_interrupt_service(self, interrupt_service: InterruptService) -> OrchestratorBuilder:
        """Provide a custom InterruptService implementation."""
        self._interrupt_service = interrupt_service
        return self

    # MARK: - Build

    def build(self) -> AgentOrchestratorInterface:
        """Build the orchestrator with configured components."""
        if self._agent is None:
            raise ValueError("Agent is required but not configured")

        from .core import AgentOrchestrator

        orchestrator = AgentOrchestrator(
            agent=cast(Any, self._agent),
            client=self._client,
            logger=self._logger or get_optional_logger(),
            tool_spec_factory=self._tool_spec_factory,
            state_compiler=self._state_compiler,
            tool_execution_service=self._tool_execution_service,
            tool_execution_orchestrator=self._tool_execution_orchestrator,
            event_stream_processor=self._event_stream_processor,
            session_service=self._session_service,
            background_executor=self._background_executor,
            interrupt_service=self._interrupt_service,
            http_timeout=self._timeout,
            pending_event_timeout_s=self._pending_event_timeout,
        )
        return cast(AgentOrchestratorInterface, orchestrator)


# MARK: - Factory Function


def create_orchestrator_for_agent(agent: Any) -> AgentOrchestratorInterface:
    """Create an orchestrator for an agent using sensible defaults."""
    return OrchestratorBuilder().with_agent(agent).build()


__all__ = [
    "OrchestratorBuilder",
    "create_orchestrator_for_agent",
]
