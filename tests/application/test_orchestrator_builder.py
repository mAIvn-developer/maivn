from __future__ import annotations

import pytest

from maivn._internal.core.orchestrator.builder import (
    OrchestratorBuilder,
    create_orchestrator_for_agent,
)


class _StubOrchestrator:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _Agent:
    name = "agent"


def test_orchestrator_builder_requires_agent() -> None:
    builder = OrchestratorBuilder()

    with pytest.raises(ValueError):
        builder.build()


def test_orchestrator_builder_wires_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    import maivn._internal.core.orchestrator.core as orchestrator_core

    monkeypatch.setattr(orchestrator_core, "AgentOrchestrator", _StubOrchestrator)

    builder = (
        OrchestratorBuilder()
        .with_agent(_Agent())
        .with_timeout(12.0)
        .with_pending_event_timeout(0.5)
    )

    orchestrator = builder.build()

    assert isinstance(orchestrator, _StubOrchestrator)
    assert orchestrator.kwargs["http_timeout"] == 12.0
    assert orchestrator.kwargs["pending_event_timeout_s"] == 0.5


def test_create_orchestrator_for_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    import maivn._internal.core.orchestrator.core as orchestrator_core

    monkeypatch.setattr(orchestrator_core, "AgentOrchestrator", _StubOrchestrator)

    orchestrator = create_orchestrator_for_agent(_Agent())

    assert isinstance(orchestrator, _StubOrchestrator)
