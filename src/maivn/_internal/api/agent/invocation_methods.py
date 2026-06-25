"""Orchestrator-routed methods for ``Agent``.

Provided as a mixin so the ``Agent`` class declaration in :mod:`.agent` stays
focused on identity, fields, and lifecycle. All invocation-time behavior
(``invoke``/``stream``/``ainvoke``/``astream``/batch/compile) lives here.
"""

# pyright: strict
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias, TypedDict, cast

from maivn_shared import (
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    ModelConfig,
    ModelTier,
    SessionOrchestrationConfig,
    SessionRequest,
    SessionResponse,
    SwarmConfig,
    SystemMessage,
    SystemToolsConfig,
)
from pydantic import BaseModel as PydanticBaseModel

from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces.orchestrator_protocol import (
    AgentOrchestratorInterface,
    JsonObject,
)

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
    from maivn._internal.utils.reporting.terminal_reporter import BaseReporter


# MARK: Types

ModelSelection: TypeAlias = ModelTier | ModelConfig
ReasoningLevel: TypeAlias = Literal["minimal", "low", "medium", "high"]
ConfigOverride: TypeAlias = dict[str, object]


class AgentInvokeKwargs(TypedDict, total=False):
    force_final_tool: bool
    targeted_tools: list[str] | None
    structured_output: type[PydanticBaseModel] | None
    model: ModelSelection | None
    force_model: str | None
    reasoning: ReasoningLevel | None
    stream_response: bool
    thread_id: str | None
    verbose: bool
    metadata: JsonObject | None
    memory_config: MemoryConfig | ConfigOverride | None
    system_tools_config: SystemToolsConfig | ConfigOverride | None
    orchestration_config: SessionOrchestrationConfig | ConfigOverride | None
    memory_assets_config: MemoryAssetsConfig | ConfigOverride | None
    swarm_config: SwarmConfig | ConfigOverride | None
    allow_private_in_system_tools: bool | None


class AgentOrchestratorInvokeKwargs(TypedDict):
    force_final_tool: bool
    targeted_tools: list[str] | None
    structured_output: type[PydanticBaseModel] | None
    model: ModelSelection | None
    force_model: str | None
    reasoning: ReasoningLevel | None
    stream_response: bool
    metadata: JsonObject | None
    memory_config: MemoryConfig | None
    system_tools_config: SystemToolsConfig | None
    orchestration_config: SessionOrchestrationConfig | None
    memory_assets_config: MemoryAssetsConfig | None
    swarm_config: SwarmConfig | None
    thread_id: str | None
    verbose: bool


class _ReporterGetter(Protocol):
    def __call__(self) -> BaseReporter | None: ...


class _AgentInvocationScope(Protocol):
    @property
    def id(self) -> str: ...

    name: str | None
    hook_execution_mode: str
    _system_message: SystemMessage | None
    _orchestrator: AgentOrchestratorInterface | None

    def _build_orchestrator(self) -> AgentOrchestratorInterface: ...

    def _get_orchestrator(self) -> AgentOrchestratorInterface: ...

    def _invoke_with_orchestrator(
        self,
        orchestrator: AgentOrchestratorInterface,
        messages: Sequence[BaseMessage],
        *,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[PydanticBaseModel] | None = None,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse: ...

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[PydanticBaseModel] | None = None,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse: ...

    def stream(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> Iterator[SSEEvent]: ...

    def _prepare_invocation_state(
        self,
        messages: Sequence[BaseMessage],
        *,
        metadata: JsonObject | None,
        memory_config: MemoryConfig | ConfigOverride | None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None,
        swarm_config: SwarmConfig | ConfigOverride | None,
        allow_private_in_system_tools: bool | None,
    ) -> InvocationState: ...

    def _validate_invoke_params(
        self,
        force_final_tool: bool,
        targeted_tools: list[str] | None,
        structured_output: type[PydanticBaseModel] | None,
    ) -> None: ...

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

    def get_swarm(self) -> object | None: ...

    def reject_reserved_memory_metadata_keys(self, metadata: object) -> None: ...

    def build_memory_asset_payloads(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]: ...

    def validate_tool_configuration(self) -> None: ...

    def list_tools(self) -> list[BaseTool]: ...


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
        orchestrator = cast(
            AgentOrchestratorInterface | None,
            getattr(self, "_orchestrator"),  # noqa: B009 - Pydantic PrivateAttr.
        )
        if orchestrator is None:
            orchestrator = self._build_orchestrator()
            setattr(self, "_orchestrator", orchestrator)  # noqa: B010 - Pydantic PrivateAttr.
        return orchestrator

    def _build_orchestrator(self) -> AgentOrchestratorInterface:
        """Build a new orchestrator instance for this agent."""
        from maivn._internal.core.orchestrator.builder import OrchestratorBuilder

        builder = OrchestratorBuilder()
        with_agent = cast(Callable[[object], OrchestratorBuilder], builder.with_agent)
        return with_agent(self).build()

    # MARK: - Sync Invocation

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[PydanticBaseModel] | None = None,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
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
            force_model=force_model,
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
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
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

        orchestrator_kwargs: AgentOrchestratorInvokeKwargs = {
            "force_final_tool": force_final_tool,
            "targeted_tools": targeted_tools,
            "structured_output": structured_output,
            "model": model,
            "force_model": force_model,
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
            response = orchestrator.invoke(
                invocation_state.prepared_messages,
                **orchestrator_kwargs,
            )
            return self._coerce_typed_result(response, structured_output, force_final_tool)

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
        except Exception as exc:  # noqa: BLE001 - after hooks must fire before re-raising
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
        return self._coerce_typed_result(result, structured_output, force_final_tool)

    def _final_tool_model(self) -> type[PydanticBaseModel] | None:
        scope = cast(_AgentInvocationScope, cast(object, self))
        try:
            for tool in scope.list_tools():
                if not getattr(tool, "final_tool", False):
                    continue
                model = getattr(tool, "model", None)
                if isinstance(model, type) and issubclass(model, PydanticBaseModel):
                    return model
        except Exception:  # noqa: BLE001 - final-tool introspection is best-effort.
            return None
        return None

    def _coerce_typed_result(
        self,
        response: SessionResponse,
        structured_output: type[PydanticBaseModel] | None,
        force_final_tool: bool,
    ) -> SessionResponse:
        if structured_output is not None:
            target: type[PydanticBaseModel] | None = structured_output
            strict = True
        elif force_final_tool:
            target = self._final_tool_model()
            strict = False
        else:
            target = None
            strict = False

        if target is None or not isinstance(response.result, dict):
            return response

        raw_result = cast(dict[str, object], response.result)
        try:
            response.result = target.model_validate(raw_result)
        except Exception as exc:
            if strict:
                _add_structured_output_validation_note(exc, target, raw_result)
                raise
        return response

    def stream(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
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
            force_model=force_model,
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
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
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
            force_model=force_model,
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
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> AsyncIterator[SSEEvent]:
        """Async wrapper around :meth:`stream` that yields events from a worker thread."""

        def _stream() -> Iterator[SSEEvent]:
            return self.stream(
                messages,
                force_final_tool=force_final_tool,
                targeted_tools=targeted_tools,
                model=model,
                force_model=force_model,
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
        input_item: object,
        invoke_kwargs: dict[str, object],
    ) -> SessionResponse:
        orchestrator = self._build_orchestrator()
        try:
            typed_kwargs = cast(AgentInvokeKwargs, cast(object, invoke_kwargs))
            return self._invoke_with_orchestrator(
                orchestrator,
                cast(Sequence[BaseMessage], input_item),
                **typed_kwargs,
            )
        finally:
            try:
                orchestrator.close()
            except (RuntimeError, OSError, AttributeError):
                pass

    def compile_state(
        self,
        messages: Sequence[BaseMessage],
        targeted_tools: list[str] | None = None,
        memory_config: MemoryConfig | ConfigOverride | None = None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None = None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None = None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None = None,
        swarm_config: SwarmConfig | ConfigOverride | None = None,
        stream_response: bool = True,
    ) -> SessionRequest:
        """Compile agent state via the AgentOrchestrator."""
        scope = cast(_AgentInvocationScope, cast(object, self))
        swarm = scope.get_swarm()
        swarm_id = getattr(swarm, "id", None)
        return self._get_orchestrator().compile_state(
            messages,
            targeted_tools=targeted_tools,
            memory_config=scope.resolve_memory_config(memory_config),
            system_tools_config=scope.resolve_system_tools_config(system_tools_config),
            orchestration_config=scope.resolve_orchestration_config(orchestration_config),
            memory_assets_config=self._resolve_memory_assets_config(
                memory_assets_config,
                default_agent_id=scope.id,
                default_swarm_id=swarm_id if isinstance(swarm_id, str) else None,
            ),
            swarm_config=self._coerce_swarm_config(swarm_config),
            stream_response=stream_response,
        )

    # MARK: - Invocation Helpers

    def _prepare_invocation_state(
        self,
        messages: Sequence[BaseMessage],
        *,
        metadata: JsonObject | None,
        memory_config: MemoryConfig | ConfigOverride | None,
        system_tools_config: SystemToolsConfig | ConfigOverride | None,
        orchestration_config: SessionOrchestrationConfig | ConfigOverride | None,
        memory_assets_config: MemoryAssetsConfig | ConfigOverride | None,
        swarm_config: SwarmConfig | ConfigOverride | None,
        allow_private_in_system_tools: bool | None,
    ) -> InvocationState:
        scope = cast(_AgentInvocationScope, cast(object, self))
        return prepare_invocation_state(
            scope,
            messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
            system_message=cast(
                SystemMessage | None,
                getattr(self, "_system_message"),  # noqa: B009 - Pydantic PrivateAttr.
            ),
        )

    @staticmethod
    def _resolve_hook_reporter(orchestrator: AgentOrchestratorInterface) -> BaseReporter | None:
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
            return cast(_ReporterGetter, getter)()
        except Exception:  # noqa: BLE001 - reporter lookup must never crash hook firing
            return None

    @staticmethod
    def _coerce_swarm_config(value: object) -> SwarmConfig | None:
        return coerce_swarm_config(value)

    def _resolve_memory_assets_config(
        self,
        override: MemoryAssetsConfig | ConfigOverride | None = None,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> MemoryAssetsConfig | None:
        return resolve_memory_assets_config(
            cast(_AgentInvocationScope, cast(object, self)),
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
        validate_invoke_params(
            cast(_AgentInvocationScope, cast(object, self)),
            force_final_tool,
            targeted_tools,
            structured_output,
        )

    def _prepare_messages(
        self,
        messages: Sequence[BaseMessage],
    ) -> list[BaseMessage]:
        """Prepare messages, injecting system message if needed."""
        return prepare_messages(
            messages,
            system_message=cast(
                SystemMessage | None,
                getattr(self, "_system_message"),  # noqa: B009 - Pydantic PrivateAttr.
            ),
        )


def _add_structured_output_validation_note(
    exc: Exception,
    target: type[PydanticBaseModel],
    raw_result: dict[str, object],
) -> None:
    """Attach a developer-facing hint while preserving the original exception type."""
    exc.add_note(_structured_output_validation_note(target, raw_result))


def _structured_output_validation_note(
    target: type[PydanticBaseModel],
    raw_result: dict[str, object],
) -> str:
    expected_fields = list(target.model_fields.keys())
    received_fields = list(raw_result.keys())
    parts = [
        f"Structured output validation failed for {target.__name__}.",
        "Expected top-level fields: "
        + f"{_format_field_names(expected_fields)}; received: "
        + f"{_format_field_names(received_fields)}.",
    ]
    nested_hints = _nested_structured_output_hints(set(expected_fields), raw_result)
    parts.extend(nested_hints)
    parts.append(
        "Return the requested structured-output model fields at the top-level "
        + "SessionResponse.result value."
    )
    return " ".join(parts)


def _nested_structured_output_hints(
    expected_fields: set[str],
    raw_result: dict[str, object],
) -> list[str]:
    hints: list[str] = []
    for key, value in raw_result.items():
        if not isinstance(value, dict):
            continue
        nested_payload = cast(dict[object, object], value)
        nested_keys = {str(nested_key) for nested_key in nested_payload}
        overlap = sorted(expected_fields.intersection(nested_keys))
        if not overlap:
            continue
        overlap_text = _format_field_names(overlap)
        if key in expected_fields:
            hints.append(
                f"Field {key!r} contains nested structured-output fields "
                + f"({overlap_text}); flatten that object or return the scalar "
                + f"expected by {key!r}."
            )
        elif expected_fields.issubset(nested_keys):
            hints.append(
                f"Nested key {key!r} contains all expected fields ({overlap_text}); "
                + "return those fields at the top-level result instead of wrapping them."
            )
        else:
            hints.append(
                f"Nested key {key!r} overlaps expected fields ({overlap_text}); "
                + "check whether a nested agent or tool result was returned without extraction."
            )
    return hints


def _format_field_names(fields: Sequence[str]) -> str:
    return ", ".join(fields) if fields else "(none)"


__all__ = ["AgentInvocationMethodsMixin"]
