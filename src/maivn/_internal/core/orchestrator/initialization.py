"""Initialization and dependency wiring for AgentOrchestrator."""

# pyright: strict
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast, runtime_checkable

from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol
from pydantic import JsonValue

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
from maivn._internal.utils.configuration import MaivnConfiguration, get_configuration
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from .events import (
    EventConsumptionCoordinator,
    OrchestratorReporterHooks,
)
from .helpers import OrchestratorConfig
from .protocols import OrchestratedAgent
from .tooling import ToolIndexCoordinator

if TYPE_CHECKING:
    from maivn._internal.adapters.networking import StreamingSSEClient
    from maivn._internal.core.application_services.orchestration import (
        tool_execution_orchestrator as orchestration_types,
    )
    from maivn._internal.core.application_services.tool_execution.tool_event_dispatcher import (
        dispatcher as dispatcher_types,
    )
    from maivn._internal.core.services import (
        HttpClientService,
        InterruptHandler,
        InterruptManager,
        ToolEventDispatcher,
    )
    from maivn._internal.core.services.agent_execution_service import AgentRegistry


# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]


class OrchestratorCallbacks(Protocol):
    def get_execution_reporter(self) -> BaseReporter | None: ...

    def get_execution_progress_task(self) -> object | None: ...

    def get_execution_swarm_name(self) -> str | None: ...

    def set_execution_reporter_context(
        self,
        reporter: BaseReporter | None,
        progress_task: object | None,
    ) -> None: ...

    def post_execution_resume(self, resume_url: str, payload: JsonObject) -> None: ...


@runtime_checkable
class _ToolExecutionTimeoutClient(Protocol):
    def get_tool_execution_timeout(self) -> float | None: ...


@dataclass(frozen=True)
class OrchestratorWiring:
    agent: OrchestratedAgent
    reporter: BaseReporter | None
    progress_task: object | None
    logger: MetricsLoggerProtocol
    client: SessionClientProtocol
    base_url: str
    config: OrchestratorConfig
    tool_spec_factory: ToolSpecFactory
    sse_client: StreamingSSEClient
    state_compiler: StateCompiler
    tool_execution: ToolExecutionService
    tool_exec_orchestrator: ToolExecutionOrchestrator
    event_processor: EventStreamProcessor
    session_service: SessionService
    background_executor: BackgroundExecutor
    interrupt_service: InterruptService
    interrupt_manager: InterruptManager
    interrupt_handler: InterruptHandler
    http_client_service: HttpClientService
    tooling: ToolIndexCoordinator
    tool_event_dispatcher: ToolEventDispatcher
    reporter_hooks: OrchestratorReporterHooks
    event_coordinator: EventConsumptionCoordinator
    state: None
    session_id: None
    client_id: None
    thread_id: None


# MARK: Client Resolution


def resolve_client(
    agent: OrchestratedAgent,
    client: SessionClientProtocol | None,
    config: MaivnConfiguration,
) -> SessionClientProtocol:
    """Resolve session client from agent, explicit client, or configuration."""
    from maivn._internal.api.client import Client as InternalClient

    if client is not None:
        return client

    default_client = agent.client
    if default_client is not None:
        return default_client

    return InternalClient.from_configuration(
        api_key=getattr(agent, "api_key", None),
        configuration=config,
    )


# MARK: Configuration Building


def build_config(
    client: SessionClientProtocol,
    config: MaivnConfiguration,
    http_timeout: float | None,
    pending_event_timeout_s: float | None,
) -> OrchestratorConfig:
    """Build orchestrator configuration from parameters and defaults."""
    client_timeout = client.timeout

    resolved_http_timeout = (
        float(http_timeout)
        if http_timeout is not None
        else float(client_timeout)
        if client_timeout is not None
        else float(config.server.timeout_seconds)
    )

    client_execution_timeout = _get_client_execution_timeout(client)
    resolved_execution_timeout = (
        float(client_execution_timeout)
        if client_execution_timeout is not None
        else float(config.execution.default_timeout_seconds)
    )

    resolved_pending = (
        float(pending_event_timeout_s)
        if pending_event_timeout_s is not None
        else float(config.execution.pending_event_timeout_seconds)
    )

    return OrchestratorConfig(
        http_timeout=resolved_http_timeout,
        execution_timeout=resolved_execution_timeout,
        pending_event_timeout_s=resolved_pending,
        max_retries=getattr(config.server, "max_retries", 3),
        enable_background_execution=bool(
            getattr(config.execution, "enable_background_execution", True)
        ),
    )


# MARK: Service Initialization


def init_orchestrator(
    orch: OrchestratorCallbacks,
    *,
    agent: OrchestratedAgent,
    client: SessionClientProtocol | None,
    http_timeout: float | None,
    pending_event_timeout_s: float | None,
    logger_override: MetricsLoggerProtocol | None,
    tool_spec_factory: ToolSpecFactory | None,
    state_compiler: StateCompiler | None,
    tool_execution_service: ToolExecutionService | None,
    tool_execution_orchestrator: ToolExecutionOrchestrator | None,
    event_stream_processor: EventStreamProcessor | None,
    session_service: SessionService | None,
    background_executor: BackgroundExecutor | None,
    interrupt_service: InterruptService | None,
) -> OrchestratorWiring:
    """Wire all dependencies onto an AgentOrchestrator instance."""
    from maivn._internal.core.services import (
        HttpClientService,
        InterruptHandler,
        InterruptManager,
        ToolEventDispatcher,
    )
    from maivn._internal.core.services.interrupt_service import (
        get_interrupt_service,
    )
    from maivn._internal.utils.logging import get_optional_logger

    config = get_configuration()

    logger = logger_override or get_optional_logger()

    resolved_client = resolve_client(agent, client, config)
    base_url = resolved_client.base_url or config.server.base_url
    orchestrator_config = build_config(
        resolved_client,
        config,
        http_timeout,
        pending_event_timeout_s,
    )

    # Core services
    resolved_tool_spec_factory = tool_spec_factory or ToolSpecFactory()
    from maivn._internal.adapters.networking import StreamingSSEClient

    sse_client = StreamingSSEClient(timeout=orchestrator_config.http_timeout)
    resolved_state_compiler = state_compiler or StateCompiler(
        tool_spec_factory=resolved_tool_spec_factory,
    )
    resolved_tool_execution = tool_execution_service or ToolExecutionService(
        logger=logger,
        reporter_supplier=orch.get_execution_reporter,
    )
    _maybe_set_agent_registry(agent, resolved_tool_execution)

    # Orchestration services
    resolved_tool_exec_orchestrator = tool_execution_orchestrator or ToolExecutionOrchestrator(
        tool_execution_service=cast(
            "orchestration_types.ToolExecutionRuntime",
            cast(object, resolved_tool_execution),
        ),
        logger=logger,
        scope=agent,
        default_timeout=orchestrator_config.execution_timeout,
        enable_background_execution=orchestrator_config.enable_background_execution,
    )
    resolved_event_processor = event_stream_processor or EventStreamProcessor(
        logger=logger,
        pending_event_timeout_s=orchestrator_config.pending_event_timeout_s,
    )
    resolved_session_service = session_service or SessionService(
        logger=logger,
    )
    resolved_background_executor = background_executor or BackgroundExecutor(
        run_inline=not orchestrator_config.enable_background_execution,
    )

    # Interrupt handling
    resolved_interrupt_service = interrupt_service or get_interrupt_service()
    interrupt_manager = InterruptManager()
    interrupt_handler = InterruptHandler(
        agent=agent,
        client=resolved_client,
        interrupt_service=resolved_interrupt_service,
        interrupt_manager=interrupt_manager,
        resume_callback=orch.post_execution_resume,
        reporter_supplier=orch.get_execution_reporter,
        progress_task_supplier=orch.get_execution_progress_task,
        logger=logger,
    )
    resolved_tool_execution.set_interrupt_service(resolved_interrupt_service)

    # HTTP client
    http_client_service = HttpClientService(
        timeout=orchestrator_config.http_timeout,
        max_retries=orchestrator_config.max_retries,
        logger=logger,
    )

    # Tool dispatching
    tooling = ToolIndexCoordinator(
        tool_execution=resolved_tool_execution,
        tool_exec_orchestrator=resolved_tool_exec_orchestrator,
        state_compiler=resolved_state_compiler,
        agent=agent,
    )
    tool_event_dispatcher = ToolEventDispatcher(
        coordinator=cast(
            "dispatcher_types.ToolEventCoordinator",
            cast(object, resolved_tool_exec_orchestrator),
        ),
        tool_execution_service=resolved_tool_execution,
        background_executor=resolved_background_executor,
        post_resume=orch.post_execution_resume,
        reporter_supplier=orch.get_execution_reporter,
        progress_task_supplier=orch.get_execution_progress_task,
        agent_count_supplier=tooling.get_agent_count,
        tool_agent_lookup=tooling.tool_agent_lookup,
        swarm_name_supplier=orch.get_execution_swarm_name,
        logger=logger,
    )

    # Event coordination
    reporter_hooks = OrchestratorReporterHooks(
        orch.get_execution_reporter,
        tool_agent_lookup=tooling.tool_agent_lookup,
        swarm_name_supplier=orch.get_execution_swarm_name,
    )
    event_coordinator = EventConsumptionCoordinator(
        client=resolved_client,
        event_processor=resolved_event_processor,
        interrupt_manager=interrupt_manager,
        interrupt_service=resolved_interrupt_service,
        tool_event_dispatcher=tool_event_dispatcher,
        interrupt_handler=interrupt_handler,
        sse_client=sse_client,
        reporter_hooks=reporter_hooks,
        set_reporter_context=orch.set_execution_reporter_context,
    )

    return OrchestratorWiring(
        agent=agent,
        reporter=None,
        progress_task=None,
        logger=logger,
        client=resolved_client,
        base_url=base_url,
        config=orchestrator_config,
        tool_spec_factory=resolved_tool_spec_factory,
        sse_client=sse_client,
        state_compiler=resolved_state_compiler,
        tool_execution=resolved_tool_execution,
        tool_exec_orchestrator=resolved_tool_exec_orchestrator,
        event_processor=resolved_event_processor,
        session_service=resolved_session_service,
        background_executor=resolved_background_executor,
        interrupt_service=resolved_interrupt_service,
        interrupt_manager=interrupt_manager,
        interrupt_handler=interrupt_handler,
        http_client_service=http_client_service,
        tooling=tooling,
        tool_event_dispatcher=tool_event_dispatcher,
        reporter_hooks=reporter_hooks,
        event_coordinator=event_coordinator,
        state=None,
        session_id=None,
        client_id=None,
        thread_id=None,
    )


# MARK: Configuration Helpers


def _get_client_execution_timeout(client: SessionClientProtocol) -> float | None:
    if not isinstance(client, _ToolExecutionTimeoutClient):
        return None
    return client.get_tool_execution_timeout()


# MARK: Registry Helper


def _maybe_set_agent_registry(
    agent: OrchestratedAgent,
    tool_execution: ToolExecutionService,
) -> None:
    """Configure agent registry on tool execution service if available."""
    swarm = agent.get_swarm()
    if swarm is None:
        return
    registry_obj = cast(object, getattr(swarm, "agent_registry", None))
    registry = cast("AgentRegistry", registry_obj if registry_obj is not None else cast(object, {}))
    tool_execution.set_agent_registry(registry)


__all__ = [
    "OrchestratorCallbacks",
    "OrchestratorWiring",
    "build_config",
    "init_orchestrator",
    "resolve_client",
]
