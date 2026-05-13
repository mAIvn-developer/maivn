"""Orchestrator-routed methods for ``Agent``.

Provided as a mixin so the ``Agent`` class declaration in :mod:`.agent` stays
focused on identity, fields, and lifecycle. All invocation-time behavior
(``invoke``/``stream``/``ainvoke``/``astream``/batch/compile) lives here.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal

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
from pydantic import BaseModel as PydanticBaseModel

from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.core.interfaces import AgentOrchestratorInterface

from ..async_stream import stream_in_worker_thread
from .hooks import (
    build_scope_hook_payload,
    get_after_scope_hooks,
    get_before_scope_hooks,
    run_scope_hooks,
    scope_hooks_enabled,
    wrap_stream_with_hooks,
)
from .invocation_helpers import (
    coerce_swarm_config,
    prepare_invocation_state,
    prepare_messages,
    resolve_memory_assets_config,
    validate_invoke_params,
)
from .invocation_state import InvocationState

if TYPE_CHECKING:
    pass


# MARK: AgentInvocationMethodsMixin


class AgentInvocationMethodsMixin:
    """Orchestrator-routed methods for ``Agent``.

    This mixin assumes the concrete class provides ``_orchestrator``,
    ``_closed`` private attributes plus ``resolve_memory_config``,
    ``resolve_system_tools_config``, ``resolve_orchestration_config``, and
    ``get_swarm`` instance methods (all of which the ``Agent`` class supplies).
    """

    # MARK: - Orchestrator

    def _get_orchestrator(self) -> AgentOrchestratorInterface:
        """Get or create cached orchestrator instance."""
        if self._orchestrator is None:  # type: ignore[attr-defined]
            self._orchestrator = self._build_orchestrator()  # type: ignore[attr-defined]
        return self._orchestrator  # type: ignore[attr-defined,return-value]

    def _build_orchestrator(self) -> AgentOrchestratorInterface:
        """Build a new orchestrator instance for this agent."""
        from maivn._internal.core.orchestrator.builder import OrchestratorBuilder

        return OrchestratorBuilder().with_agent(self).build()

    # MARK: - Sync Invocation

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

        reporter = self._resolve_hook_reporter(orchestrator)
        payload = build_scope_hook_payload(self, invocation_state)
        run_scope_hooks(
            get_before_scope_hooks(self, invocation_state),
            payload,
            stage="before",
            reporter=reporter,
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
                reporter=reporter,
            )
            raise

        payload["stage"] = "after"
        payload["result"] = result
        run_scope_hooks(
            get_after_scope_hooks(self, invocation_state),
            payload,
            stage="after",
            reporter=reporter,
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
        reporter = self._resolve_hook_reporter(self._get_orchestrator())
        return wrap_stream_with_hooks(
            stream_iter, self, invocation_state, payload, reporter=reporter
        )

    # MARK: - Async Invocation

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

    # MARK: - Batch / Compile

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
            memory_config=self.resolve_memory_config(memory_config),  # type: ignore[attr-defined]
            system_tools_config=self.resolve_system_tools_config(system_tools_config),  # type: ignore[attr-defined]
            orchestration_config=self.resolve_orchestration_config(orchestration_config),  # type: ignore[attr-defined]
            memory_assets_config=self._resolve_memory_assets_config(
                memory_assets_config,
                default_agent_id=self.id,  # type: ignore[attr-defined]
                default_swarm_id=getattr(self.get_swarm(), "id", None),  # type: ignore[attr-defined]
            ),
            swarm_config=self._coerce_swarm_config(swarm_config),
            stream_response=stream_response,
        )

    # MARK: - Invocation Helpers

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
    ) -> InvocationState:
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

    @staticmethod
    def _resolve_hook_reporter(orchestrator: AgentOrchestratorInterface) -> Any:
        """Best-effort lookup of the orchestrator's reporter for hook emission.

        Returns ``None`` if the orchestrator doesn't expose ``_get_reporter``
        (custom orchestrator implementations) or if no reporter is wired —
        :func:`run_scope_hooks` and :func:`wrap_stream_with_hooks` treat
        ``None`` as "don't emit", so the hook still runs locally.
        """
        getter = getattr(orchestrator, "_get_reporter", None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:  # noqa: BLE001 - reporter lookup must never crash hook firing
            return None

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

    def _prepare_messages(
        self,
        messages: Sequence[BaseMessage],
    ) -> list[BaseMessage]:
        """Prepare messages, injecting system message if needed."""
        return prepare_messages(self, messages)


__all__ = ["AgentInvocationMethodsMixin"]
