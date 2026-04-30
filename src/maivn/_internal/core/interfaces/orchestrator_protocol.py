"""Protocol for agent orchestrators."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Literal, Protocol

from maivn_shared import (
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SessionRequest,
    SessionResponse,
    SwarmConfig,
    SystemToolsConfig,
)
from pydantic import BaseModel

from maivn._internal.core import SSEEvent

# MARK: - Protocol Definition


class AgentOrchestratorInterface(Protocol):
    """Protocol defining the interface for agent orchestrators.

    This interface ensures that orchestrators provide consistent methods
    for compiling state and invoking agent execution.
    """

    # MARK: - State Compilation

    def compile_state(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> SessionRequest:
        """Compile messages and tools into a SessionRequest.

        Args:
            messages: Messages to send to the agent.
            force_final_tool: If True, returns result from final_tool; otherwise last task.
            targeted_tools: Optional list of tool names to execute.
            structured_output: Optional Pydantic model for structured output validation.
            model: LLM model selection: 'fast', 'balanced', 'max'.
            reasoning: Reasoning level: 'minimal', 'low', 'medium', 'high'.
            stream_response: Whether to stream synthesized response updates.
            status_messages: Whether to emit status messages at swarm lifecycle milestones.
            thread_id: Optional thread identifier for conversation continuity.
            metadata: Optional metadata to merge into the session request.
            memory_config: Optional memory configuration.
            system_tools_config: Optional system-tool configuration.
            orchestration_config: Optional orchestration loop controls.
            memory_assets_config: Optional memory asset payloads.
            swarm_config: Optional swarm orchestration configuration.

        Returns:
            SessionRequest ready for submission.
        """
        ...

    # MARK: - Invocation

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> SessionResponse:
        """Execute agent invocation.

        Args:
            messages: Messages to send to the agent.
            force_final_tool: If True, returns result from final_tool; otherwise last task.
            targeted_tools: Optional list of tool names to execute.
            structured_output: Optional Pydantic model for structured output validation.
            model: LLM model selection: 'fast', 'balanced', 'max'.
            reasoning: Reasoning level: 'minimal', 'low', 'medium', 'high'.
            stream_response: Whether to stream synthesized response updates.
            thread_id: Optional thread identifier for conversation continuity.
            verbose: If True, enables verbose output during execution.
            metadata: Optional metadata to merge into the session request.
            memory_config: Optional memory configuration.
            system_tools_config: Optional system-tool configuration.
            orchestration_config: Optional orchestration loop controls.
            memory_assets_config: Optional memory asset payloads.
            swarm_config: Optional swarm orchestration configuration.

        Returns:
            SessionResponse with results.
        """
        ...

    def stream(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> Iterator[SSEEvent]:
        """Stream raw SSE events while executing an agent invocation.

        Args mirror :meth:`invoke` except ``structured_output``, which remains invoke-only.
        Additionally accepts ``status_messages`` to control lifecycle notifications.
        The returned iterator yields events as they arrive, including the ``final`` event.
        """
        ...

    def invoke_compiled_state(
        self,
        state: SessionRequest,
        *,
        thread_id: str | None = None,
        verbose: bool = False,
        compilation_elapsed_s: float | None = None,
    ) -> SessionResponse:
        """Execute a pre-compiled session state.

        Args:
            state: Pre-compiled SessionRequest to execute.
            thread_id: Optional thread identifier for conversation continuity.
            verbose: If True, enables verbose output during execution.
            compilation_elapsed_s: Optional compilation time for logging purposes.

        Returns:
            SessionResponse with results.
        """
        ...

    def stream_compiled_state(
        self,
        state: SessionRequest,
        *,
        thread_id: str | None = None,
        verbose: bool = False,
        compilation_elapsed_s: float | None = None,
    ) -> Iterator[SSEEvent]:
        """Stream raw SSE events for a pre-compiled state.

        The iterator yields every event from the server stream, including the ``final`` event.
        """
        ...

    # MARK: - Swarm Agent Tools

    def _register_swarm_agent_tools(self, agent_tools: list) -> None:
        """Register swarm agent invocation tools for SDK-side execution.

        Args:
            agent_tools: List of agent invocation tools to register.
        """
        ...

    # MARK: - Resource Management

    def close(self) -> None:
        """Release any resources held by the orchestrator."""
        ...
