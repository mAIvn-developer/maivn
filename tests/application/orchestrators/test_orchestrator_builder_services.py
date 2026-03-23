from __future__ import annotations

from typing import Any

import pytest

import maivn._internal.core.orchestrator.builder as builder_module
import maivn._internal.core.orchestrator.core as orchestrator_module
from maivn._internal.core.orchestrator.builder import OrchestratorBuilder


class _StubOrchestrator:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _Agent:
    name = "agent"


def test_orchestrator_builder_passes_explicit_service_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator_module, "AgentOrchestrator", _StubOrchestrator)

    client = object()
    logger = object()
    tool_spec_factory = object()
    state_compiler = object()
    tool_execution_service = object()
    tool_execution_orchestrator = object()
    event_stream_processor = object()
    session_service = object()
    background_executor = object()
    interrupt_service = object()

    orchestrator = (
        OrchestratorBuilder()
        .with_agent(_Agent())
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
    default_logger = object()
    monkeypatch.setattr(orchestrator_module, "AgentOrchestrator", _StubOrchestrator)
    monkeypatch.setattr(builder_module, "get_optional_logger", lambda: default_logger)

    orchestrator = OrchestratorBuilder().with_agent(_Agent()).build()

    assert isinstance(orchestrator, _StubOrchestrator)
    assert orchestrator.kwargs["logger"] is default_logger
