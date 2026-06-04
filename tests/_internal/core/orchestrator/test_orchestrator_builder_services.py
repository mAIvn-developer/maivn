# pyright: strict
from __future__ import annotations

from typing import cast

import pytest
from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import MetricsLoggerProtocol
from typing_extensions import final

import maivn._internal.core.orchestrator.builder as builder_module
import maivn._internal.core.orchestrator.core as orchestrator_module
from maivn._internal.api.agent import Agent
from maivn._internal.core.orchestrator.builder import OrchestratorBuilder
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


@final
class _StubOrchestrator:
    kwargs: dict[str, object]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


@final
class _Agent:
    name: str = "agent"


def test_orchestrator_builder_passes_explicit_service_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator_module, "AgentOrchestrator", _StubOrchestrator)

    # Each `with_*` setter is typed against a specific Protocol/concrete service.
    # The test verifies pass-through wiring, so we use bare `object()` placeholders
    # cast at the boundary. `object` is the universal supertype, so a single cast
    # suffices (no double-cast needed).
    client = cast(SessionClientProtocol, object())
    logger = cast(MetricsLoggerProtocol, object())
    tool_spec_factory = cast(ToolSpecFactory, object())
    state_compiler = cast(StateCompiler, object())
    tool_execution_service = cast(ToolExecutionService, object())
    tool_execution_orchestrator = cast(ToolExecutionOrchestrator, object())
    event_stream_processor = cast(EventStreamProcessor, object())
    session_service = cast(SessionService, object())
    background_executor = cast(BackgroundExecutor, object())
    interrupt_service = cast(InterruptService, object())

    orchestrator = (
        OrchestratorBuilder()
        .with_agent(cast(Agent, cast(object, _Agent())))
        .with_client(client)
        .with_logger(logger)
        .with_tool_spec_factory(tool_spec_factory)
        .with_state_compiler(state_compiler)
        .with_tool_execution_service(tool_execution_service)
        .with_tool_execution_orchestrator(tool_execution_orchestrator)
        .with_event_stream_processor(event_stream_processor)
        .with_session_service(session_service)
        .with_background_executor(background_executor)
        .with_interrupt_service(interrupt_service)
        .build()
    )

    assert isinstance(orchestrator, _StubOrchestrator)
    assert orchestrator.kwargs == {
        "agent": orchestrator.kwargs["agent"],
        "client": client,
        "logger": logger,
        "tool_spec_factory": tool_spec_factory,
        "state_compiler": state_compiler,
        "tool_execution_service": tool_execution_service,
        "tool_execution_orchestrator": tool_execution_orchestrator,
        "event_stream_processor": event_stream_processor,
        "session_service": session_service,
        "background_executor": background_executor,
        "interrupt_service": interrupt_service,
        "http_timeout": None,
        "pending_event_timeout_s": None,
    }
    assert isinstance(orchestrator.kwargs["agent"], _Agent)


def test_orchestrator_builder_uses_optional_logger_when_not_explicitly_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_logger: object = object()
    monkeypatch.setattr(orchestrator_module, "AgentOrchestrator", _StubOrchestrator)
    monkeypatch.setattr(builder_module, "get_optional_logger", lambda: default_logger)

    orchestrator = OrchestratorBuilder().with_agent(cast(Agent, cast(object, _Agent()))).build()

    assert isinstance(orchestrator, _StubOrchestrator)
    assert orchestrator.kwargs["logger"] is default_logger
