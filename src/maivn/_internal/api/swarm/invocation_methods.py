"""Orchestrator-routed methods for ``Swarm``.

Provided as a mixin so the ``Swarm`` class declaration in :mod:`.swarm` stays
focused on identity, fields, and member management. All invocation-time
behavior (``invoke``/``stream``/``ainvoke``/``astream``, state preparation,
tool registration) lives here.
"""

# pyright: strict
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, cast

from maivn_shared import (
    BaseMessage,
    MemoryConfig,
    ModelConfig,
    ModelTier,
    SessionOrchestrationConfig,
    SessionRequest,
    SessionResponse,
    SystemMessage,
    SystemToolsConfig,
    ToolSpec,
)

from maivn._internal.core.application_services.state_compilation import DynamicToolFactory
from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.core.entities.tools import AgentTool, BaseTool
from maivn._internal.core.interfaces.orchestrator_protocol import (
    AgentOrchestratorInterface,
    JsonObject,
)
from maivn._internal.core.orchestrator.builder import OrchestratorBuilder
from maivn._internal.core.tool_specs import ToolSpecFactory

from ..async_stream import stream_in_worker_thread
from ..base_scope import BaseScope
from .metadata import (
    SwarmLike,
    build_agent_roster_entry,
    build_invocation_tool_map,
    enrich_state_metadata,
)
from .validation import has_swarm_final_tools

if TYPE_CHECKING:
    from ..agent.agent import Agent


# MARK: Types

ModelSelection: TypeAlias = ModelTier | ModelConfig
ReasoningLevel: TypeAlias = Literal["minimal", "low", "medium", "high"]
ConfigOverride: TypeAlias = dict[str, object]


class _SwarmInvocationScope(Protocol):
    @property
    def id(self) -> str: ...

    name: str | None
    description: str | None
    system_prompt: str | SystemMessage | None
    agents: Sequence[Agent]
    _system_message: SystemMessage | None

    def validate_on_invoke(self) -> None: ...

    def _validate_force_final_tool_request(self, force_final_tool: bool) -> None: ...

    def resolve_memory_config(self, override: object = None) -> MemoryConfig | None: ...

    def resolve_system_tools_config(
        self,
        override: object = None,
        *,
        allow_private_in_system_tools: bool | None = None,
    ) -> SystemToolsConfig | None: ...

    def resolve_orchestration_config(
        self,
        override: object = None,
    ) -> SessionOrchestrationConfig | None: ...

    def list_tools(self) -> list[BaseTool]: ...

    def build_memory_asset_payloads(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...


# MARK: SwarmInvocationMethodsMixin


class SwarmInvocationMethodsMixin:
    """Orchestrator-routed methods for ``Swarm``.

    Assumes the concrete class provides ``agents`` plus ``validate_on_invoke``,
    ``resolve_memory_config``, ``resolve_system_tools_config``,
    ``resolve_orchestration_config``, ``list_tools``, and
    ``_validate_force_final_tool_request`` (all supplied by ``Swarm``).
    """

    def validate_on_invoke(self) -> None:
        """Implemented by ``Swarm``."""
        raise NotImplementedError

    def _validate_force_final_tool_request(self, _force_final_tool: bool) -> None:
        """Implemented by ``Swarm``."""
        raise NotImplementedError

    # MARK: - Sync Invocation

    def invoke(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        """Invoke the swarm with the given messages."""
        self.validate_on_invoke()
        self._validate_force_final_tool_request(force_final_tool)

        orchestrator, state = self._prepare_execution_state(
            messages,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            thread_id=thread_id,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
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
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> Iterator[SSEEvent]:
        """Stream raw SSE events while executing the swarm."""
        self.validate_on_invoke()
        self._validate_force_final_tool_request(force_final_tool)

        orchestrator, state = self._prepare_execution_state(
            messages,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            thread_id=thread_id,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            status_messages=status_messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

        return orchestrator.stream_compiled_state(
            state,
            thread_id=thread_id,
            verbose=verbose,
        )

    # MARK: - Async Invocation

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        """Async wrapper around :meth:`invoke`."""
        return await asyncio.to_thread(
            self.invoke,
            messages,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            thread_id=thread_id,
            verbose=verbose,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

    async def astream(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Async wrapper around :meth:`stream`."""

        def _stream() -> Iterator[SSEEvent]:
            return self.stream(
                messages,
                model=model,
                force_model=force_model,
                reasoning=reasoning,
                force_final_tool=force_final_tool,
                stream_response=stream_response,
                status_messages=status_messages,
                thread_id=thread_id,
                verbose=verbose,
                metadata=metadata,
                memory_config=memory_config,
                system_tools_config=system_tools_config,
                orchestration_config=orchestration_config,
                allow_private_in_system_tools=allow_private_in_system_tools,
            )

        async for event in stream_in_worker_thread(_stream):
            yield event

    # MARK: - Execution Helpers

    def _build_orchestrator(
        self,
        agent: Agent,
    ) -> AgentOrchestratorInterface:
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

        system_message = cast(
            SystemMessage | None,
            getattr(self, "_system_message"),  # noqa: B009 - Pydantic PrivateAttr.
        )
        has_system = any(isinstance(m, SystemMessage) for m in messages_list)

        if not has_system and system_message is not None:
            messages_list = [system_message, *messages_list]

        return messages_list

    def _prepare_execution_state(
        self,
        messages: Sequence[BaseMessage] | BaseMessage,
        *,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        thread_id: str | None = None,
        force_final_tool: bool = False,
        stream_response: bool = True,
        status_messages: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> tuple[AgentOrchestratorInterface, SessionRequest]:
        scope = cast(_SwarmInvocationScope, cast(object, self))
        entry_agent = scope.agents[0]
        prepared_messages = self._prepare_messages(messages)
        orchestrator = self._build_orchestrator(entry_agent)

        state = orchestrator.compile_state(
            prepared_messages,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            thread_id=thread_id,
            force_final_tool=force_final_tool,
            stream_response=stream_response,
            status_messages=status_messages,
            memory_config=scope.resolve_memory_config(memory_config),
            system_tools_config=scope.resolve_system_tools_config(
                system_tools_config,
                allow_private_in_system_tools=allow_private_in_system_tools,
            ),
            orchestration_config=scope.resolve_orchestration_config(orchestration_config),
            metadata=metadata,
        )

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
        memory_config: MemoryConfig | ConfigOverride | None = None,
    ) -> None:
        """Configure state with metadata and tools."""
        swarm = cast(SwarmLike, cast(object, self))
        enrich_state_metadata(swarm, state, memory_config=memory_config)
        self._register_tools(orchestrator, state, entry_agent, force_final_tool=force_final_tool)

    # MARK: - Metadata Delegation

    def _enrich_state_metadata(
        self,
        state: SessionRequest,
        *,
        memory_config: MemoryConfig | ConfigOverride | None = None,
    ) -> None:
        """Enrich state metadata with swarm information."""
        swarm = cast(SwarmLike, cast(object, self))
        enrich_state_metadata(swarm, state, memory_config=memory_config)

    def _build_invocation_tool_map(self) -> dict[str, str]:
        """Build mapping of agent IDs to invocation tool IDs."""
        return build_invocation_tool_map(cast(SwarmLike, cast(object, self)))

    def _build_agent_roster_entry(
        self,
        agent: Agent,
        invocation_tool_map: dict[str, str],
    ) -> dict[str, object]:
        """Build a roster entry for an agent."""
        scope = cast(_SwarmInvocationScope, cast(object, self))
        agent_id_to_name = {
            member.id: member.name or "" for member in scope.agents if member.id and member.name
        }
        return cast(
            dict[str, object],
            build_agent_roster_entry(
                cast(SwarmLike, cast(object, self)),
                agent,
                invocation_tool_map,
                agent_id_to_name=agent_id_to_name,
            ).model_dump(exclude_none=True),
        )

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
        tools: list[ToolSpec] = []

        self._register_agent_invocation_tools(orchestrator, tools, entry_agent, tool_spec_factory)

        if force_final_tool or self._has_swarm_final_tools():
            self._register_swarm_tools_for_force_final(tools, entry_agent, tool_spec_factory)

        state.tools = tools

    def _register_agent_invocation_tools(
        self,
        orchestrator: AgentOrchestratorInterface,
        tools: list[ToolSpec],
        entry_agent: Agent,
        tool_spec_factory: ToolSpecFactory,
    ) -> None:
        """Create and register agent invocation tools."""
        invocation_tools = DynamicToolFactory().create_swarm_agent_invocation_tools(
            cast(BaseScope, cast(object, self))
        )
        if not invocation_tools:
            return

        for tool in invocation_tools:
            tool_spec = tool_spec_factory.create(agent_id=entry_agent.id, tool=tool)
            tools.append(tool_spec)

        orchestrator.register_swarm_agent_tools(cast(list[AgentTool], invocation_tools))

    def _register_swarm_tools_for_force_final(
        self,
        tools: list[ToolSpec],
        entry_agent: Agent,
        tool_spec_factory: ToolSpecFactory,
    ) -> None:
        """Register swarm-level tools when a final tool is available or forced."""
        swarm_tools = cast(_SwarmInvocationScope, cast(object, self)).list_tools()
        if not swarm_tools:
            return

        for tool in swarm_tools:
            tool_specs = tool_spec_factory.create_all(agent_id=entry_agent.id, tool=tool)
            tools.extend(tool_specs)

    def _has_swarm_final_tools(self) -> bool:
        """Check if swarm has any tools marked as final_tool."""
        return has_swarm_final_tools(cast(_SwarmInvocationScope, cast(object, self)))


__all__ = ["SwarmInvocationMethodsMixin"]
