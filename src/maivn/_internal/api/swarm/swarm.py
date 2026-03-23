"""Swarm scope for coordinating multiple agents.
Provides DI-friendly registries and shared tool access for groups of agents.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

from maivn_shared import (
    BaseMessage,
    MemoryConfig,
    SessionRequest,
    SessionResponse,
    SystemMessage,
)
from pydantic import Field, PrivateAttr, model_validator
from typing_extensions import Self

from maivn._internal.adapters.repositories import AgentRepo
from maivn._internal.core.application_services.state_compilation import (
    DynamicToolFactory,
)
from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.core.interfaces.orchestrator_protocol import (
    AgentOrchestratorInterface,
)
from maivn._internal.core.interfaces.repositories import (
    AgentRepoInterface,
    ToolRepoInterface,
)
from maivn._internal.core.orchestrator.builder import (
    OrchestratorBuilder,
)
from maivn._internal.core.registrars import AgentRegistrar
from maivn._internal.core.tool_specs import ToolSpecFactory

from ..base_scope import BaseScope
from .metadata import (
    _build_agent_roster_entry as _swarm_build_agent_roster_entry,
)
from .metadata import (
    _build_invocation_tool_map as _swarm_build_invocation_tool_map,
)
from .metadata import (
    enrich_state_metadata,
)

if TYPE_CHECKING:
    from ..agent import Agent


# MARK: Swarm


class Swarm(BaseScope):
    """Swarm managing multiple Agents via DI and registrars."""

    agents: list[Agent] = Field(
        default_factory=list,
        description="List of agents in the swarm",
    )

    _agent_repo: AgentRepoInterface = PrivateAttr()
    _agent_registrar: AgentRegistrar = PrivateAttr()

    # MARK: - Lifecycle

    def model_post_init(self, context: Any) -> None:
        """Initialize swarm services and registries."""
        super().model_post_init(context)

        self._agent_repo = getattr(self, "agent_repo", None) or AgentRepo()
        self._agent_registrar = AgentRegistrar(repo=self._agent_repo)

        if self.agents:
            initial_agents = list(self.agents)
            self.agents = []
            for agent in initial_agents:
                self.add_agent(agent)

    @model_validator(mode="after")
    def _validate_swarm_state(self) -> Self:
        return self

    # MARK: - Repository Access

    @property
    def member_agent_repository(self) -> AgentRepoInterface:
        """Access the agent repository."""
        return self._agent_repo

    @property
    def member_tool_repository(self) -> ToolRepoInterface:
        """Access the tool repository."""
        return self._tool_repo

    # MARK: - Agent Management

    def add_agent(self, agent: Agent) -> None:
        """Add an agent to the swarm."""
        self._agent_registrar(self, agent)

    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve an agent by ID."""
        return self._agent_repo.get_agent(agent_id)  # type: ignore[return-value]

    def list_agents(self) -> list[Agent]:
        """List all agents in the swarm."""
        return self._agent_repo.list_agents()  # type: ignore[return-value]

    # MARK: - Validation

    def validate_on_invoke(self) -> None:
        """Validate swarm configuration before invocation."""
        self.validate_tool_configuration()
        self._ensure_has_agents()

    # MARK: - Invocation

    def invoke(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: Any = None,
        reasoning: Any = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        """Invoke the swarm with the given messages."""
        self.validate_on_invoke()
        self._validate_force_final_tool_request(force_final_tool)

        orchestrator, state = self._prepare_execution_state(
            messages,
            model=model,
            reasoning=reasoning,
            thread_id=thread_id,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            metadata=metadata,
            memory_config=memory_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

        return orchestrator.invoke_compiled_state(
            state,
            thread_id=thread_id,
            verbose=verbose,
        )

    def stream(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: Any = None,
        reasoning: Any = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> Iterator[SSEEvent]:
        """Stream raw SSE events while executing the swarm."""
        self.validate_on_invoke()
        self._validate_force_final_tool_request(force_final_tool)

        orchestrator, state = self._prepare_execution_state(
            messages,
            model=model,
            reasoning=reasoning,
            thread_id=thread_id,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            status_messages=status_messages,
            metadata=metadata,
            memory_config=memory_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

        return orchestrator.stream_compiled_state(
            state,
            thread_id=thread_id,
            verbose=verbose,
        )

    # MARK: - Execution Helpers

    def _ensure_has_agents(self) -> None:
        """Ensure the swarm has at least one agent."""
        if not self.agents:
            raise ValueError("Swarm.invoke requires at least one Agent in the swarm.")

    def _build_orchestrator(self, agent: Agent) -> AgentOrchestratorInterface:
        """Build an orchestrator for the given agent."""
        return OrchestratorBuilder().with_agent(agent).build()

    def _prepare_messages(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
    ) -> list[BaseMessage]:
        """Prepare messages list, injecting system message if needed."""
        if isinstance(messages, BaseMessage):
            messages_list = [messages]
        else:
            messages_list = list(messages)

        system_message = getattr(self, "_system_message", None)
        has_system = any(isinstance(m, SystemMessage) for m in messages_list)

        if not has_system and system_message is not None:
            messages_list = [system_message, *messages_list]

        return messages_list

    def _prepare_execution_state(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: Any = None,
        reasoning: Any = None,
        thread_id: str | None = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        status_messages: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> tuple[AgentOrchestratorInterface, SessionRequest]:
        entry_agent = self.agents[0]
        prepared_messages = self._prepare_messages(messages)
        orchestrator = self._build_orchestrator(entry_agent)

        state = orchestrator.compile_state(
            prepared_messages,
            model=model,
            reasoning=reasoning,
            thread_id=thread_id,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            status_messages=status_messages,
            memory_config=self.resolve_memory_config(memory_config),
        )

        merged_metadata = dict(state.metadata or {})
        if metadata:
            merged_metadata.update(metadata)
        if allow_private_in_system_tools is not None:
            merged_metadata["allow_private_data_in_system_tools"] = bool(
                allow_private_in_system_tools
            )
        merged_metadata.setdefault("allow_private_data_placeholders_in_system_tools", True)
        if merged_metadata:
            state.metadata = merged_metadata

        self._configure_state(
            state,
            orchestrator,
            entry_agent,
            force_final_tool=force_final_tool,
            memory_config=memory_config,
        )
        return orchestrator, state

    def _configure_state(
        self,
        state: SessionRequest,
        orchestrator: AgentOrchestratorInterface,
        entry_agent: Agent,
        *,
        force_final_tool: bool = False,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
    ) -> None:
        """Configure state with metadata and tools."""
        enrich_state_metadata(self, state, memory_config=memory_config)
        self._register_tools(orchestrator, state, entry_agent, force_final_tool=force_final_tool)

    # MARK: - Metadata Delegation

    def _enrich_state_metadata(
        self,
        state: SessionRequest,
        *,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
    ) -> None:
        """Enrich state metadata with swarm information."""
        enrich_state_metadata(self, state, memory_config=memory_config)

    def _build_invocation_tool_map(self) -> dict[str, str]:
        """Build mapping of agent IDs to invocation tool IDs."""
        return _swarm_build_invocation_tool_map(self)

    def _build_agent_roster_entry(
        self,
        agent: Agent,
        invocation_tool_map: dict[str, str],
    ) -> dict[str, Any]:
        """Build a roster entry for an agent."""
        return _swarm_build_agent_roster_entry(self, agent, invocation_tool_map)

    # MARK: - Tool Registration

    def _register_tools(
        self,
        orchestrator: AgentOrchestratorInterface,
        state: SessionRequest,
        entry_agent: Agent,
        *,
        force_final_tool: bool = False,
    ) -> None:
        """Register agent-invocation tools with the orchestrator."""
        tool_spec_factory = ToolSpecFactory()
        tools: list[Any] = []

        self._register_agent_invocation_tools(orchestrator, tools, entry_agent, tool_spec_factory)

        if force_final_tool or self._has_swarm_final_tools():
            self._register_swarm_tools_for_force_final(tools, entry_agent, tool_spec_factory)

        state.tools = tools

    def _register_agent_invocation_tools(
        self,
        orchestrator: AgentOrchestratorInterface,
        tools: list[Any],
        entry_agent: Agent,
        tool_spec_factory: ToolSpecFactory,
    ) -> None:
        """Create and register agent invocation tools."""
        invocation_tools = DynamicToolFactory().create_swarm_agent_invocation_tools(self)
        if not invocation_tools:
            return

        for tool in invocation_tools:
            tool_spec = tool_spec_factory.create(agent_id=entry_agent.id, tool=tool)
            tools.append(tool_spec)

        orchestrator._register_swarm_agent_tools(invocation_tools)

    def _register_swarm_tools_for_force_final(
        self,
        tools: list[Any],
        entry_agent: Agent,
        tool_spec_factory: ToolSpecFactory,
    ) -> None:
        """Register swarm-level tools when a final tool is available or forced."""
        swarm_tools = self.list_tools()
        if not swarm_tools:
            return

        for tool in swarm_tools:
            tool_specs = tool_spec_factory.create_all(agent_id=entry_agent.id, tool=tool)
            tools.extend(tool_specs)

    def _has_swarm_final_tools(self) -> bool:
        """Check if swarm has any tools marked as final_tool."""
        return any(bool(getattr(tool, "final_tool", False)) for tool in self.list_tools())

    def _validate_force_final_tool_request(self, force_final_tool: bool) -> None:
        """Validate force_final_tool usage for swarm invocations."""
        if not force_final_tool:
            return

        swarm_final_tools = [
            tool for tool in self.list_tools() if bool(getattr(tool, "final_tool", False))
        ]
        final_output_agents = [
            agent for agent in self.agents if bool(getattr(agent, "use_as_final_output", False))
        ]
        if swarm_final_tools and final_output_agents:
            raise ValueError(
                "Swarm.invoke(force_final_tool=True) cannot be combined with both "
                "swarm final_tool(s) and use_as_final_output=True on swarm agents."
            )

        if not swarm_final_tools and not final_output_agents:
            raise ValueError(
                "Swarm.invoke(force_final_tool=True) requires either a swarm tool with "
                "final_tool=True or an agent with use_as_final_output=True."
            )


def _rebuild_swarm_model() -> None:
    from ..agent import Agent

    Swarm.model_rebuild(_types_namespace={"Agent": Agent})


_rebuild_swarm_model()


__all__ = ["Swarm"]
