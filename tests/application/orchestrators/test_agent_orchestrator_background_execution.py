from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from maivn._internal.core.orchestrator.core import AgentOrchestrator


@dataclass(frozen=True)
class _ServerConfig:
    base_url: str = "https://api.local"
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class _ExecutionConfig:
    default_timeout_seconds: float = 123.0
    pending_event_timeout_seconds: float = 1.0
    enable_background_execution: bool = True


@dataclass(frozen=True)
class _Config:
    server: _ServerConfig
    execution: _ExecutionConfig


class _StubClient:
    base_url: str = "https://client.local"
    timeout: float | None = None

    def get_tool_execution_timeout(self) -> float | None:
        return None


class _StubAgent:
    client = _StubClient()
    api_key: str | None = None
    timeout: float | None = None
    max_results: int | None = None
    name: str = "stub"
    description: str | None = None

    def get_swarm(self):  # noqa: ANN001
        return None


def test_agent_orchestrator_disables_background_execution() -> None:
    cfg = _Config(
        server=_ServerConfig(),
        execution=_ExecutionConfig(enable_background_execution=False),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(_StubAgent(), logger=None)

    assert orchestrator._background_executor._run_inline is True
    assert orchestrator._tool_exec_orchestrator._enable_background_execution is False


def test_agent_orchestrator_enables_background_execution() -> None:
    cfg = _Config(
        server=_ServerConfig(),
        execution=_ExecutionConfig(enable_background_execution=True),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(_StubAgent(), logger=None)

    assert orchestrator._background_executor._run_inline is False
    assert orchestrator._tool_exec_orchestrator._enable_background_execution is True
