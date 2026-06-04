"""Unified ``AgentOrchestrator`` implementation.
Coordinates session lifecycle, SSE event streaming, and tool execution.
"""

# pyright: strict
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Literal, TypeAlias

from maivn_shared import (
    BaseMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionClientProtocol,
    SessionExecutionConfig,
    SessionOrchestrationConfig,
    SessionRequest,
    SessionResponse,
    SwarmConfig,
    SystemToolsConfig,
)
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol
from pydantic import BaseModel, JsonValue

from maivn._internal.core import SessionEndpoints, SSEEvent
from maivn._internal.core.entities.tools.agent_tool import AgentTool
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
from maivn._internal.utils.configuration import get_configuration
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from .events import (
    EventConsumptionCoordinator,
    OrchestratorReporterHooks,
)
from .execution import (
    execute_invoke,
    execute_stream,
)
from .helpers import (
    OrchestratorConfig,
    coerce_tool_list,
)
from .initialization import init_orchestrator
from .protocols import OrchestratedAgent
from .tooling import ToolIndexCoordinator

if TYPE_CHECKING:
    from maivn._internal.adapters.networking import StreamingSSEClient
    from maivn._internal.core.services import (
        HttpClientService,
        InterruptHandler,
        InterruptManager,
        ToolEventDispatcher,
    )

logger = logging.getLogger(__name__)


# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
ProgressTask: TypeAlias = object


# MARK: Orchestrator


class AgentOrchestrator:
    """Coordinates agent execution against the maivn-server."""

    # MARK: - Dependencies

    client: SessionClientProtocol
    base_url: str
    _config: OrchestratorConfig
    _logger: MetricsLoggerProtocol
    _http_client_service: HttpClientService
    _background_executor: BackgroundExecutor
    agent: OrchestratedAgent
    _tool_spec_factory: ToolSpecFactory
    _sse_client: StreamingSSEClient
    _state_compiler: StateCompiler
    _tool_execution: ToolExecutionService
    _tool_exec_orchestrator: ToolExecutionOrchestrator
    _event_processor: EventStreamProcessor
    _session_service: SessionService
    _interrupt_service: InterruptService
    _interrupt_manager: InterruptManager
    _interrupt_handler: InterruptHandler
    _tool_event_dispatcher: ToolEventDispatcher
    _reporter_hooks: OrchestratorReporterHooks
    _event_coordinator: EventConsumptionCoordinator
    _reporter: BaseReporter | None = None
    _progress_task: ProgressTask | None = None
    _tooling: ToolIndexCoordinator
    _state: SessionRequest | None = None
    _session_id: str | None = None
    _client_id: str | None = None
    _thread_id: str | None = None

    def __init__(
        self,
        agent: OrchestratedAgent,
        *,
        client: SessionClientProtocol | None = None,
        http_timeout: float | None = None,
        logger: MetricsLoggerProtocol | None = None,
        tool_spec_factory: ToolSpecFactory | None = None,
        state_compiler: StateCompiler | None = None,
        pending_event_timeout_s: float | None = None,
        tool_execution_service: ToolExecutionService | None = None,
        tool_execution_orchestrator: ToolExecutionOrchestrator | None = None,
        event_stream_processor: EventStreamProcessor | None = None,
        session_service: SessionService | None = None,
        background_executor: BackgroundExecutor | None = None,
        interrupt_service: InterruptService | None = None,
    ) -> None:
        wiring = init_orchestrator(
            self,
            agent=agent,
            client=client,
            http_timeout=http_timeout,
            pending_event_timeout_s=pending_event_timeout_s,
            logger_override=logger,
            tool_spec_factory=tool_spec_factory,
            state_compiler=state_compiler,
            tool_execution_service=tool_execution_service,
            tool_execution_orchestrator=tool_execution_orchestrator,
            event_stream_processor=event_stream_processor,
            session_service=session_service,
            background_executor=background_executor,
            interrupt_service=interrupt_service,
        )
        self.agent = wiring.agent
        self._reporter = wiring.reporter
        self._progress_task = wiring.progress_task
        self._logger = wiring.logger
        self.client = wiring.client
        self.base_url = wiring.base_url
        self._config = wiring.config
        self._tool_spec_factory = wiring.tool_spec_factory
        self._sse_client = wiring.sse_client
        self._state_compiler = wiring.state_compiler
        self._tool_execution = wiring.tool_execution
        self._tool_exec_orchestrator = wiring.tool_exec_orchestrator
        self._event_processor = wiring.event_processor
        self._session_service = wiring.session_service
        self._background_executor = wiring.background_executor
        self._interrupt_service = wiring.interrupt_service
        self._interrupt_manager = wiring.interrupt_manager
        self._interrupt_handler = wiring.interrupt_handler
        self._http_client_service = wiring.http_client_service
        self._tooling = wiring.tooling
        self._tool_event_dispatcher = wiring.tool_event_dispatcher
        self._reporter_hooks = wiring.reporter_hooks
        self._event_coordinator = wiring.event_coordinator
        self._state = wiring.state
        self._session_id = wiring.session_id
        self._client_id = wiring.client_id
        self._thread_id = wiring.thread_id

    # MARK: - Public Properties

    @property
    def http_timeout(self) -> float:
        """HTTP timeout in seconds."""
        return self._config.http_timeout

    @property
    def timeout(self) -> float:
        """Default execution timeout in seconds."""
        return self._config.execution_timeout

    # MARK: - Public API

    def compile_state(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> SessionRequest:
        """Compile agent state without executing."""
        _ = self.agent.compile_tools()
        swarm = self.agent.get_swarm()
        if swarm:
            _ = swarm.compile_tools()

        tools = list(self.agent.list_tools())
        if swarm:
            tools.extend(swarm.list_tools())

        self._tooling.rebuild_tool_index(list(coerce_tool_list(tools)))

        timeout = self.agent.timeout if self.agent.timeout is not None else self.timeout
        timeout_int = int(timeout)
        execution_config = self._build_session_execution_config()

        state = self._state_compiler.compile_state(
            messages=list(messages),
            tools=tools,
            scope=self.agent,
            timeout=timeout_int,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=structured_output,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            stream_response=stream_response,
            status_messages=status_messages,
            max_results=self.agent.max_results,
            execution_config=execution_config,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            metadata=metadata,
        )
        self._state = state
        self._tool_exec_orchestrator.update_messages(messages)
        self._thread_id = thread_id

        self._tooling.rebuild_tool_index_with_dynamic_tools(tools)
        self._tooling.build_tool_agent_mapping(swarm)

        return state

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> SessionResponse:
        """Execute the agent end-to-end and return the final response."""
        state, elapsed = self._compile_execution_state(
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=structured_output,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            stream_response=stream_response,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            thread_id=thread_id,
        )
        return self.invoke_compiled_state(
            state,
            thread_id=thread_id,
            verbose=verbose,
            compilation_elapsed_s=elapsed,
        )

    def stream(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> Iterator[SSEEvent]:
        """Execute the agent and stream raw SSE events as they arrive."""
        state, elapsed = self._compile_execution_state(
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=None,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            stream_response=stream_response,
            status_messages=status_messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            thread_id=thread_id,
        )
        return self.stream_compiled_state(
            state,
            thread_id=thread_id,
            verbose=verbose,
            compilation_elapsed_s=elapsed,
        )

    def invoke_compiled_state(
        self,
        state: SessionRequest,
        *,
        thread_id: str | None = None,
        verbose: bool = False,
        compilation_elapsed_s: float | None = None,
    ) -> SessionResponse:
        """Execute a pre-compiled session state."""
        return execute_invoke(
            self,
            state,
            thread_id=thread_id,
            verbose=verbose,
            compilation_elapsed_s=compilation_elapsed_s,
        )

    def stream_compiled_state(
        self,
        state: SessionRequest,
        *,
        thread_id: str | None = None,
        verbose: bool = False,
        compilation_elapsed_s: float | None = None,
    ) -> Iterator[SSEEvent]:
        """Execute a pre-compiled state and stream raw SSE events."""
        return execute_stream(
            self,
            state,
            thread_id=thread_id,
            verbose=verbose,
            compilation_elapsed_s=compilation_elapsed_s,
        )

    # MARK: - Session Management

    def _start_session(self, state: SessionRequest) -> SessionEndpoints:
        """Start a new session with the server."""
        thread_id = self._thread_id or self.client.get_thread_id(create_if_missing=False)
        self._client_id = f"sdk-{uuid.uuid4()}"
        payload = self._session_service.build_payload(
            state=state,
            client_id=self._client_id,
            thread_id=thread_id,
        )
        endpoints = self._session_service.start_session(
            client=self.client,
            payload=payload,
        )
        self._session_id = endpoints.session_id
        return endpoints

    def start_execution_session(self, state: SessionRequest) -> SessionEndpoints:
        """Start a server session for the execution helpers."""
        return self._start_session(state)

    # MARK: - Tool Registration

    def register_swarm_agent_tools(self, agent_tools: list[AgentTool]) -> None:
        self._tooling.register_swarm_agent_tools(agent_tools)

    # MARK: - Internal Helpers

    def set_execution_reporter(self, reporter: BaseReporter | None) -> None:
        self._reporter = reporter

    def set_execution_state(self, state: SessionRequest, thread_id: str | None) -> None:
        self._state = state
        if thread_id is not None:
            self._thread_id = thread_id

    def clear_execution_interrupts(self) -> None:
        self._interrupt_manager.clear_collected_interrupts()

    def clear_execution_progress_task(self) -> None:
        self._progress_task = None

    def configure_execution_reporter_hooks(
        self,
        *,
        is_nested: bool,
        allow_nested_response_stream: bool,
    ) -> None:
        self._reporter_hooks.configure_execution_context(
            is_nested=is_nested,
            allow_nested_response_stream=allow_nested_response_stream,
        )

    def consume_execution_events(
        self,
        endpoints: SessionEndpoints,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: ProgressTask | None,
        on_event: Callable[[SSEEvent], None] | None = None,
    ) -> JsonObject:
        return self._event_coordinator.consume_events(
            endpoints,
            timeout,
            reporter,
            progress_task,
            on_event=on_event,
        )

    def _compile_execution_state(
        self,
        messages: Sequence[BaseMessage],
        *,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        metadata: JsonObject | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> tuple[SessionRequest, float]:
        t0 = time.perf_counter()
        state = self.compile_state(
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=structured_output,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            stream_response=stream_response,
            status_messages=status_messages,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            thread_id=thread_id,
        )
        return state, time.perf_counter() - t0

    def _build_session_execution_config(self) -> SessionExecutionConfig | None:
        """Build SDK execution context that should travel as typed config."""
        timezone_metadata = self._build_timezone_metadata()
        config = SessionExecutionConfig(
            client_timezone=timezone_metadata.get("client_timezone"),
            sdk_deployment_timezone=timezone_metadata.get("sdk_deployment_timezone"),
        )
        return config if config.is_configured() else None

    def _build_timezone_metadata(self) -> dict[str, str]:
        """Build timezone execution config from client configuration."""
        tz_metadata: dict[str, str] = {}
        client_timezone = getattr(self.client, "client_timezone", None)
        if isinstance(client_timezone, str) and client_timezone.strip():
            tz_metadata["client_timezone"] = client_timezone

        sdk_deployment_timezone = getattr(
            self.client,
            "deployment_timezone",
            None,
        )
        if not (isinstance(sdk_deployment_timezone, str) and sdk_deployment_timezone.strip()):
            try:
                sdk_deployment_timezone = get_configuration().server.deployment_timezone
            except Exception:  # noqa: BLE001 - timezone metadata is optional
                sdk_deployment_timezone = None
        if isinstance(sdk_deployment_timezone, str) and sdk_deployment_timezone.strip():
            tz_metadata["sdk_deployment_timezone"] = sdk_deployment_timezone
        return tz_metadata

    def get_execution_reporter(self) -> BaseReporter | None:
        """Return the current reporter instance."""
        return self._reporter

    def _get_reporter(self) -> BaseReporter | None:
        """Return the current reporter instance."""
        return self.get_execution_reporter()

    def get_execution_progress_task(self) -> ProgressTask | None:
        """Return the current progress task instance."""
        return self._progress_task

    def _get_progress_task(self) -> ProgressTask | None:
        """Return the current progress task instance."""
        return self.get_execution_progress_task()

    def get_execution_swarm_name(self) -> str | None:
        """Return the swarm name if the agent belongs to a swarm."""
        swarm = self.agent.get_swarm()
        if swarm is None:
            return None
        return getattr(swarm, "name", None) or swarm.__class__.__name__

    def _get_swarm_name(self) -> str | None:
        """Return the swarm name if the agent belongs to a swarm."""
        return self.get_execution_swarm_name()

    def set_execution_reporter_context(
        self,
        reporter: BaseReporter | None,
        progress_task: ProgressTask | None,
    ) -> None:
        self._reporter = reporter
        self._progress_task = progress_task

    def _set_reporter_context(
        self,
        reporter: BaseReporter | None,
        progress_task: ProgressTask | None,
    ) -> None:
        self.set_execution_reporter_context(reporter, progress_task)

    def post_execution_resume(self, resume_url: str, payload: JsonObject) -> None:
        """Send resume payload to server via HTTP POST."""
        try:
            self._http_client_service.post_resume(
                resume_url,
                payload,
                self.client,
            )
        except Exception as exc:  # noqa: BLE001 - resume POST failures are logged, not fatal
            self._logger.error(
                "[POST_RESUME] Failed to post resume payload: %s",
                exc,
            )

    def _post_resume(self, resume_url: str, payload: JsonObject) -> None:
        """Send resume payload to server via HTTP POST."""
        self.post_execution_resume(resume_url, payload)

    # MARK: - Cleanup

    def close(self) -> None:
        try:
            self._http_client_service.close()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
        try:
            self._background_executor.shutdown(wait=False)
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass

    def __del__(self) -> None:
        self.close()


__all__ = ["AgentOrchestrator", "OrchestratorConfig"]
