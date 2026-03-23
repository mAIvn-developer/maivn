"""Initialization and dependency wiring for AgentOrchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol

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

from .events import (
    EventConsumptionCoordinator,
    OrchestratorReporterHooks,
)
from .helpers import OrchestratorConfig
from .tooling import ToolIndexCoordinator

if TYPE_CHECKING:
    from maivn._internal.api.agent import Agent


# MARK: Client Resolution


def resolve_client(
    agent: Agent,
    client: SessionClientProtocol | None,
    config: Any,
) -> SessionClientProtocol:
    """Resolve session client from agent, explicit client, or configuration."""
    from maivn._internal.api.client import Client as InternalClient

    if client is not None:
        return client

    default_client: SessionClientProtocol | None = getattr(
        agent,
        "client",
        None,
    )
    if default_client is not None:
        return default_client

    return InternalClient.from_configuration(
        api_key=getattr(agent, "api_key", None),
        configuration=config,
    )


# MARK: Configuration Building


def build_config(
    client: SessionClientProtocol,
    config: Any,
    http_timeout: float | None,
    pending_event_timeout_s: float | None,
) -> OrchestratorConfig:
    """Build orchestrator configuration from parameters and defaults."""
    client_timeout = getattr(client, "timeout", None)

    resolved_http_timeout = (
        float(http_timeout)
        if http_timeout is not None
        else float(client_timeout)
        if client_timeout is not None
        else float(config.server.timeout_seconds)
    )

    client_execution_timeout = getattr(
        client,
        "get_tool_execution_timeout",
        lambda: None,
    )()
    resolved_execution_timeout = (
        float(client_execution_timeout)
        if client_execution_timeout is not None
        else float(config.execution.default_timeout_seconds)
    )

    resolved_pending = (
        pending_event_timeout_s
        if pending_event_timeout_s is not None
        else config.execution.pending_event_timeout_seconds
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
    orch: Any,
    *,
    agent: Agent,
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
) -> None:
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

    orch.agent = agent
    orch._reporter = None
    orch._progress_task = None
    orch._logger = logger_override or get_optional_logger()

    orch.client = resolve_client(agent, client, config)
    orch.base_url = getattr(orch.client, "base_url", None) or config.server.base_url
    orch._config = build_config(
        orch.client,
        config,
        http_timeout,
        pending_event_timeout_s,
    )

    # Core services
    orch._tool_spec_factory = tool_spec_factory or ToolSpecFactory()
    from maivn._internal.adapters.networking import StreamingSSEClient

    orch._sse_client = StreamingSSEClient(timeout=orch._config.http_timeout)
    orch._state_compiler = state_compiler or StateCompiler(
        tool_spec_factory=orch._tool_spec_factory,
    )
    orch._tool_execution = tool_execution_service or ToolExecutionService(
        logger=orch._logger,
    )
    _maybe_set_agent_registry(orch)

    # Orchestration services
    orch._tool_exec_orchestrator = tool_execution_orchestrator or ToolExecutionOrchestrator(
        tool_execution_service=orch._tool_execution,
        logger=orch._logger,
        scope=orch.agent,
        default_timeout=orch._config.execution_timeout,
        enable_background_execution=orch._config.enable_background_execution,
    )
    orch._event_processor = event_stream_processor or EventStreamProcessor(
        logger=orch._logger,
        pending_event_timeout_s=orch._config.pending_event_timeout_s,
    )
    orch._session_service = session_service or SessionService(
        logger=orch._logger,
    )
    orch._background_executor = background_executor or BackgroundExecutor(
        run_inline=not orch._config.enable_background_execution,
    )

    # Interrupt handling
    orch._interrupt_service = interrupt_service or get_interrupt_service()
    orch._interrupt_manager = InterruptManager()
    orch._interrupt_handler = InterruptHandler(
        agent=orch.agent,
        client=orch.client,
        interrupt_service=orch._interrupt_service,
        interrupt_manager=orch._interrupt_manager,
        resume_callback=orch._post_resume,
        reporter_supplier=orch._get_reporter,
        progress_task_supplier=orch._get_progress_task,
        logger=orch._logger,
    )
    orch._tool_execution.set_interrupt_service(orch._interrupt_service)

    # HTTP client
    orch._http_client_service = HttpClientService(
        timeout=orch._config.http_timeout,
        max_retries=orch._config.max_retries,
        logger=orch._logger,
    )

    # Tool dispatching
    orch._tooling = ToolIndexCoordinator(
        tool_execution=orch._tool_execution,
        tool_exec_orchestrator=orch._tool_exec_orchestrator,
        state_compiler=orch._state_compiler,
        agent=orch.agent,
    )
    orch._tool_event_dispatcher = ToolEventDispatcher(
        coordinator=orch._tool_exec_orchestrator,
        tool_execution_service=orch._tool_execution,
        background_executor=orch._background_executor,
        post_resume=orch._post_resume,
        reporter_supplier=orch._get_reporter,
        progress_task_supplier=orch._get_progress_task,
        agent_count_supplier=orch._tooling.get_agent_count,
        tool_agent_lookup=orch._tooling.tool_agent_lookup,
        swarm_name_supplier=orch._get_swarm_name,
        logger=orch._logger,
    )

    # Event coordination
    orch._reporter_hooks = OrchestratorReporterHooks(
        orch._get_reporter,
        tool_agent_lookup=orch._tooling.tool_agent_lookup,
        swarm_name_supplier=orch._get_swarm_name,
    )
    orch._event_coordinator = EventConsumptionCoordinator(
        client=orch.client,
        event_processor=orch._event_processor,
        interrupt_manager=orch._interrupt_manager,
        interrupt_service=orch._interrupt_service,
        tool_event_dispatcher=orch._tool_event_dispatcher,
        interrupt_handler=orch._interrupt_handler,
        sse_client=orch._sse_client,
        reporter_hooks=orch._reporter_hooks,
        set_reporter_context=orch._set_reporter_context,
    )

    # Session state
    orch._state = None
    orch._session_id = None
    orch._client_id = None
    orch._thread_id = None


# MARK: Registry Helper


def _maybe_set_agent_registry(orch: Any) -> None:
    """Configure agent registry on tool execution service if available."""
    swarm = getattr(orch.agent, "get_swarm", lambda: None)()
    if swarm and hasattr(orch._tool_execution, "set_agent_registry"):
        registry = getattr(swarm, "agent_registry", None) or {}
        orch._tool_execution.set_agent_registry(registry)


__all__ = ["build_config", "init_orchestrator", "resolve_client"]
