# pyright: strict
from __future__ import annotations

from typing import cast

import pytest

from maivn._internal.api.agent import Agent
from maivn._internal.core.orchestrator.builder import (
    OrchestratorBuilder,
    create_orchestrator_for_agent,
)


class _StubOrchestrator:
    kwargs: dict[str, object]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _Agent:
    name: str = "agent"


def test_orchestrator_builder_requires_agent() -> None:
    builder = OrchestratorBuilder()

    with pytest.raises(ValueError, match="Agent is required"):
        _ = builder.build()


def test_orchestrator_builder_wires_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    import maivn._internal.core.orchestrator.core as orchestrator_core

    monkeypatch.setattr(orchestrator_core, "AgentOrchestrator", _StubOrchestrator)

    builder = (
        OrchestratorBuilder()
        .with_agent(cast(Agent, cast(object, _Agent())))
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

    orchestrator = create_orchestrator_for_agent(cast(Agent, cast(object, _Agent())))

    assert isinstance(orchestrator, _StubOrchestrator)
