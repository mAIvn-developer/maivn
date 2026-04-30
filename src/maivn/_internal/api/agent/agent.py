"""Core Agent class implementation."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal

from maivn_shared import (
    BaseDependency,
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SessionRequest,
    SessionResponse,
    SwarmConfig,
    SystemToolsConfig,
)
from maivn_shared.domain.entities.dependencies import AwaitForDependency, ReevaluateDependency
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, PrivateAttr, field_validator

from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces import AgentOrchestratorInterface
from maivn._internal.core.services.team_dependencies import (
    add_team_dependency,
    add_team_execution_control,
    resolve_team_control_reference,
)

from ..async_stream import stream_in_worker_thread
from ..base_scope import BaseScope
from .client_cache import get_or_create_client
from .hooks import (
    build_scope_hook_payload,
    get_after_scope_hooks,
    get_before_scope_hooks,
    run_scope_hooks,
    scope_hooks_enabled,
    wrap_stream_with_hooks,
)
from .invocation_helpers import (
    build_memory_assets_config,
    coerce_memory_assets_config,
    coerce_swarm_config,
    collect_all_tools,
    prepare_invocation_state,
    prepare_messages,
    resolve_memory_assets_config,
    validate_final_tool_exists,
    validate_invoke_params,
)
from .invocation_state import InvocationState as _InvocationState

if TYPE_CHECKING:
    from ..client import Client
    from ..swarm import Swarm


# MARK: Agent


class Agent(BaseScope):
    """Agent with DI-friendly construction.

    Holds api_key and SDK Client, delegates invocation to AgentOrchestrator,
    and supports swarm membership.
    """

    # MARK: - Fields

    api_key: str | None = Field(
        default=None,
        description="API key for server authentication.",
    )
    client: Client | None = Field(
        default=None,
        description="Optional Client; created from api_key if missing.",
    )
    timeout: float | None = Field(
        default=None,
        description="Default timeout in seconds. None uses system default.",
    )
    max_results: int | None = Field(
        default=None,
        description="Maximum tools to return from semantic search.",
    )
    use_as_final_output: bool = Field(
        default=False,
        description=(
            "When part of a Swarm invocation, force this agent's output to be the final "
            "response (only one Swarm member may set this)."
        ),
    )
    included_nested_synthesis: bool | Literal["auto"] = Field(
        default="auto",
        description=("Control nested synthesis behavior for this agent when invoked by a Swarm."),
    )
    tools: list[Any] = Field(
        default_factory=list,
        description="Tools registered on this agent at construction time.",
        exclude=True,
    )

    _swarm: Swarm | None = PrivateAttr(default=None)
    _orchestrator: AgentOrchestratorInterface | None = PrivateAttr(default=None)
    _closed: bool = PrivateAttr(default=False)
    _team_dependencies: list[BaseDependency] = PrivateAttr(default_factory=list)
    _team_execution_controls: list[AwaitForDependency | ReevaluateDependency] = PrivateAttr(
        default_factory=list
    )

    # MARK: - Properties

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent."""
        return self.id

    # MARK: - Lifecycle

    def model_post_init(self, context: Any) -> None:
        """Initialize client from api_key if needed."""
        super().model_post_init(context)
        self._initialize_client()
        self._register_initial_tools()

    def _initialize_client(self) -> None:
        """Initialize client from api_key or validate existing client."""
        if self.api_key and not self.client:
            self.client = self._get_or_create_client(self.api_key)
        elif not self.api_key and not self.client:
            raise ValueError("Agent requires either a Client instance or an api_key.")

    @field_validator("included_nested_synthesis", mode="before")
    @classmethod
    def _normalize_included_nested_synthesis(cls, value: Any) -> bool | Literal["auto"]:
        """Normalize include-nested-synthesis mode."""
        if value is None:
            return "auto"
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "auto":
                return "auto"
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("included_nested_synthesis must be True, False, or 'auto'")

    @staticmethod
    def _get_or_create_client(api_key: str) -> Client:
        """Get cached client or create new one."""
        return get_or_create_client(api_key)

    # MARK: - Tool Management

    def add_tool(
        self,
        tool: BaseTool | Callable[..., Any] | type[PydanticBaseModel],
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: Callable[[dict[str, Any]], Any] | None = None,
        after_execute: Callable[[dict[str, Any]], Any] | None = None,
    ) -> BaseTool:
        """Register a callable, Pydantic model, or prebuilt tool on this agent."""
        registered_tool = super().add_tool(
            tool=tool,
            name=name,
            description=description,
            always_execute=always_execute,
            final_tool=final_tool,
            tags=tags,
            before_execute=before_execute,
            after_execute=after_execute,
        )
        self._remember_registered_tool(registered_tool)
        return registered_tool

    def _register_initial_tools(self) -> None:
        initial_tools = list(self.tools)
        self.tools = []
        for tool in initial_tools:
            self.add_tool(tool)

    def _remember_registered_tool(self, tool: BaseTool) -> None:
        tool_id = getattr(tool, "tool_id", None)
        for registered_tool in self.tools:
            if tool_id is not None and getattr(registered_tool, "tool_id", None) == tool_id:
                return
            if registered_tool is tool:
                return
        self.tools.append(tool)

    # MARK: - Swarm

    def get_swarm(self) -> Swarm | None:
        """Get parent swarm if agent belongs to one."""
        return self._swarm

    def _add_team_dependency(self, dependency: BaseDependency) -> None:
        """Attach dependency metadata for Swarm team invocation."""
        add_team_dependency(self, dependency)

    def _add_team_execution_control(
        self,
        control: AwaitForDependency | ReevaluateDependency,
    ) -> None:
        """Attach execution-control metadata for Swarm team invocation."""
        add_team_execution_control(self, control)

    def _resolve_team_control_reference(self, ref: Any) -> tuple[str, str]:
        """Resolve Swarm agent/tool refs for team execution-control decorators."""
        swarm = self.get_swarm()
        if swarm is None:
            raise ValueError(
                "Team execution controls require the agent to be registered with a Swarm."
            )
        return resolve_team_control_reference(swarm, ref)

    # MARK: - Orchestration

    def _get_orchestrator(self) -> AgentOrchestratorInterface:
        """Get or create cached orchestrator instance."""
        if self._orchestrator is None:
            self._orchestrator = self._build_orchestrator()
        return self._orchestrator

    def _build_orchestrator(self) -> AgentOrchestratorInterface:
        """Build a new orchestrator instance for this agent."""
        from maivn._internal.core.orchestrator.builder import OrchestratorBuilder

        return OrchestratorBuilder().with_agent(self).build()

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[PydanticBaseModel] | None = None,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None = None,
        swarm_config: SwarmConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        """Invoke the agent through the AgentOrchestrator."""
        return self._invoke_with_orchestrator(
            self._get_orchestrator(),
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=structured_output,
            model=model,
            reasoning=reasoning,
            stream_response=stream_response,
            thread_id=thread_id,
            verbose=verbose,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

    def _invoke_with_orchestrator(
        self,
        orchestrator: AgentOrchestratorInterface,
        messages: Sequence[BaseMessage],
        *,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[PydanticBaseModel] | None = None,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None = None,
        swarm_config: SwarmConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        self._validate_invoke_params(force_final_tool, targeted_tools, structured_output)
        invocation_state = self._prepare_invocation_state(
            messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

        orchestrator_kwargs = {
            "force_final_tool": force_final_tool,
            "targeted_tools": targeted_tools,
            "structured_output": structured_output,
            "model": model,
            "reasoning": reasoning,
            "stream_response": stream_response,
            "metadata": invocation_state.merged_metadata or None,
            "memory_config": invocation_state.resolved_memory_config,
            "system_tools_config": invocation_state.resolved_system_tools_config,
            "orchestration_config": invocation_state.resolved_orchestration_config,
            "memory_assets_config": invocation_state.resolved_memory_assets_config,
            "swarm_config": invocation_state.resolved_swarm_config,
            "thread_id": thread_id,
            "verbose": verbose,
        }

        if not scope_hooks_enabled(invocation_state):
            return orchestrator.invoke(invocation_state.prepared_messages, **orchestrator_kwargs)

        payload = build_scope_hook_payload(self, invocation_state)
        run_scope_hooks(
            get_before_scope_hooks(self, invocation_state),
            payload,
            stage="before",
        )

        try:
            result = orchestrator.invoke(invocation_state.prepared_messages, **orchestrator_kwargs)
        except Exception as exc:  # noqa: BLE001
            payload["stage"] = "after"
            payload["error"] = exc
            run_scope_hooks(
                get_after_scope_hooks(self, invocation_state),
                payload,
                stage="after",
            )
            raise

        payload["stage"] = "after"
        payload["result"] = result
        run_scope_hooks(
            get_after_scope_hooks(self, invocation_state),
            payload,
            stage="after",
        )
        return result

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
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None = None,
        swarm_config: SwarmConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> Iterator[SSEEvent]:
        """Stream raw SSE events while executing this agent."""
        self._validate_invoke_params(force_final_tool, targeted_tools, structured_output=None)
        invocation_state = self._prepare_invocation_state(
            messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

        stream_iter = self._get_orchestrator().stream(
            invocation_state.prepared_messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            model=model,
            reasoning=reasoning,
            stream_response=stream_response,
            status_messages=status_messages,
            metadata=invocation_state.merged_metadata or None,
            memory_config=invocation_state.resolved_memory_config,
            system_tools_config=invocation_state.resolved_system_tools_config,
            orchestration_config=invocation_state.resolved_orchestration_config,
            memory_assets_config=invocation_state.resolved_memory_assets_config,
            swarm_config=invocation_state.resolved_swarm_config,
            thread_id=thread_id,
            verbose=verbose,
        )

        if not scope_hooks_enabled(invocation_state):
            return stream_iter

        payload = build_scope_hook_payload(self, invocation_state)
        return wrap_stream_with_hooks(stream_iter, self, invocation_state, payload)

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[PydanticBaseModel] | None = None,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None = None,
        swarm_config: SwarmConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        """Async wrapper around :meth:`invoke` that runs the synchronous call in a thread."""
        return await asyncio.to_thread(
            self.invoke,
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=structured_output,
            model=model,
            reasoning=reasoning,
            stream_response=stream_response,
            thread_id=thread_id,
            verbose=verbose,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

    async def astream(
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
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None = None,
        swarm_config: SwarmConfig | dict[str, Any] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Async wrapper around :meth:`stream` that yields events from a worker thread."""

        def _stream() -> Iterator[SSEEvent]:
            return self.stream(
                messages,
                force_final_tool=force_final_tool,
                targeted_tools=targeted_tools,
                model=model,
                reasoning=reasoning,
                stream_response=stream_response,
                status_messages=status_messages,
                thread_id=thread_id,
                verbose=verbose,
                metadata=metadata,
                memory_config=memory_config,
                system_tools_config=system_tools_config,
                orchestration_config=orchestration_config,
                memory_assets_config=memory_assets_config,
                swarm_config=swarm_config,
                allow_private_in_system_tools=allow_private_in_system_tools,
            )

        async for event in stream_in_worker_thread(_stream):
            yield event

    # MARK: - Invocation Helpers

    def _invoke_batch_item(
        self,
        input_item: Any,
        invoke_kwargs: dict[str, Any],
    ) -> SessionResponse:
        orchestrator = self._build_orchestrator()
        try:
            return self._invoke_with_orchestrator(
                orchestrator,
                input_item,
                **invoke_kwargs,
            )
        finally:
            close = getattr(orchestrator, "close", None)
            if callable(close):
                try:
                    close()
                except (RuntimeError, OSError, AttributeError):
                    pass

    def _prepare_invocation_state(
        self,
        messages: Sequence[BaseMessage],
        *,
        metadata: dict[str, Any] | None,
        memory_config: MemoryConfig | dict[str, Any] | None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None,
        swarm_config: SwarmConfig | dict[str, Any] | None,
        allow_private_in_system_tools: bool | None,
    ) -> _InvocationState:
        return prepare_invocation_state(
            self,
            messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

    def _build_memory_assets_config(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> MemoryAssetsConfig | None:
        return build_memory_assets_config(
            self,
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )

    @staticmethod
    def _coerce_memory_assets_config(value: Any) -> MemoryAssetsConfig | None:
        return coerce_memory_assets_config(value)

    @staticmethod
    def _coerce_swarm_config(value: Any) -> SwarmConfig | None:
        return coerce_swarm_config(value)

    def _resolve_memory_assets_config(
        self,
        override: Any = None,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> MemoryAssetsConfig | None:
        return resolve_memory_assets_config(
            self,
            override,
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )

    def _validate_invoke_params(
        self,
        force_final_tool: bool,
        targeted_tools: list[str] | None,
        structured_output: type[PydanticBaseModel] | None,
    ) -> None:
        """Validate invocation parameters for mutual exclusivity."""
        validate_invoke_params(self, force_final_tool, targeted_tools, structured_output)

    def _validate_final_tool_exists(self) -> None:
        """Ensure at least one final_tool exists when force_final_tool is True."""
        validate_final_tool_exists(self)

    def _collect_all_tools(self) -> list[Any]:
        """Collect all tools from agent and parent swarm."""
        return collect_all_tools(self)

    def _prepare_messages(
        self,
        messages: Sequence[BaseMessage],
    ) -> list[BaseMessage]:
        """Prepare messages, injecting system message if needed."""
        return prepare_messages(self, messages)

    def compile_state(
        self,
        messages: Sequence[BaseMessage],
        targeted_tools: list[str] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
        memory_assets_config: MemoryAssetsConfig | dict[str, Any] | None = None,
        swarm_config: SwarmConfig | dict[str, Any] | None = None,
        stream_response: bool = True,
    ) -> SessionRequest:
        """Compile agent state via the AgentOrchestrator."""
        return self._get_orchestrator().compile_state(
            messages,
            targeted_tools=targeted_tools,
            memory_config=self.resolve_memory_config(memory_config),
            system_tools_config=self.resolve_system_tools_config(system_tools_config),
            orchestration_config=self.resolve_orchestration_config(orchestration_config),
            memory_assets_config=self._resolve_memory_assets_config(
                memory_assets_config,
                default_agent_id=self.id,
                default_swarm_id=getattr(self.get_swarm(), "id", None),
            ),
            swarm_config=self._coerce_swarm_config(swarm_config),
            stream_response=stream_response,
        )

    # MARK: - Cleanup

    def close(self) -> None:
        """Release underlying orchestrator resources."""
        if self._closed:
            return
        self._closed = True
        try:
            self.close_mcp_servers()
        except Exception:
            pass
        orchestrator = getattr(self, "_orchestrator", None)
        if orchestrator is None:
            return
        if not hasattr(orchestrator, "close"):
            return
        try:
            orchestrator.close()
        except (RuntimeError, OSError, AttributeError):
            pass

    def __del__(self) -> None:
        """Cleanup on garbage collection."""
        if sys.is_finalizing():
            return
        try:
            self.close()
        except Exception:
            pass


def _rebuild_agent_model() -> None:
    from ..client import Client

    Agent.model_rebuild(_types_namespace={"Client": Client})


_rebuild_agent_model()
