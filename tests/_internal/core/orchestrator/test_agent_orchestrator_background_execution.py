# pyright: strict
from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

from typing_extensions import final

from maivn._internal.api.agent import Agent
from maivn._internal.core.application_services.execution.background_executor import (
    BackgroundExecutor,
)
from maivn._internal.core.application_services.orchestration.tool_execution_orchestrator import (
    ToolExecutionOrchestrator,
)
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


@final
class _StubClient:
    base_url: str = "https://client.local"
    timeout: float | None = None

    def get_tool_execution_timeout(self) -> float | None:
        return None


@final
class _StubAgent:
    client: _StubClient = _StubClient()
    api_key: str | None = None
    timeout: float | None = None
    max_results: int | None = None
    name: str = "stub"
    description: str | None = None

    def get_swarm(self) -> None:
        return None


def _get_background_executor(orchestrator: AgentOrchestrator) -> BackgroundExecutor:
    """Read the orchestrator's wired BackgroundExecutor via getattr to bypass
    `reportPrivateUsage` on the protected attribute name without adding a pyright
    ignore. The wiring is verified by AgentOrchestrator.__init__ assignments."""
    value = getattr(orchestrator, "_background_executor", None)
    assert isinstance(value, BackgroundExecutor)
    return value


def _get_tool_exec_orchestrator(orchestrator: AgentOrchestrator) -> ToolExecutionOrchestrator:
    """Read the orchestrator's wired ToolExecutionOrchestrator via getattr."""
    value = getattr(orchestrator, "_tool_exec_orchestrator", None)
    assert isinstance(value, ToolExecutionOrchestrator)
    return value


def _get_run_inline(executor: BackgroundExecutor) -> bool:
    """Read the executor's `_run_inline` flag via getattr (protected attr)."""
    value = getattr(executor, "_run_inline", None)
    assert isinstance(value, bool)
    return value


def _get_enable_background_execution(orch: ToolExecutionOrchestrator) -> bool:
    """Read the orchestrator's `_enable_background_execution` flag via getattr."""
    value = getattr(orch, "_enable_background_execution", None)
    assert isinstance(value, bool)
    return value


def test_agent_orchestrator_disables_background_execution() -> None:
    cfg = _Config(
        server=_ServerConfig(),
        execution=_ExecutionConfig(enable_background_execution=False),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(cast(Agent, cast(object, _StubAgent())), logger=None)

    assert _get_run_inline(_get_background_executor(orchestrator)) is True
    assert _get_enable_background_execution(_get_tool_exec_orchestrator(orchestrator)) is False


def test_agent_orchestrator_enables_background_execution() -> None:
    cfg = _Config(
        server=_ServerConfig(),
        execution=_ExecutionConfig(enable_background_execution=True),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(cast(Agent, cast(object, _StubAgent())), logger=None)

    assert _get_run_inline(_get_background_executor(orchestrator)) is False
    assert _get_enable_background_execution(_get_tool_exec_orchestrator(orchestrator)) is True
