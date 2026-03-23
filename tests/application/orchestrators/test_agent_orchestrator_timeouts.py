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


class _StubClientWithHttpTimeout:
    """Client with HTTP timeout but no execution timeout."""

    base_url: str = "https://client.local"
    timeout: float | None = 17.0

    def get_tool_execution_timeout(self) -> float | None:
        return None


class _StubClientWithExecutionTimeout:
    """Client with execution timeout configured."""

    base_url: str = "https://client.local"
    timeout: float | None = 17.0

    def get_tool_execution_timeout(self) -> float:
        return 300.0


class _StubAgentWithClientHttpTimeout:
    client = _StubClientWithHttpTimeout()
    api_key: str | None = None
    timeout: float | None = None
    max_results: int | None = None
    name: str = "stub"
    description: str | None = None

    def get_swarm(self):  # noqa: ANN001
        return None


class _StubAgentWithClientExecutionTimeout:
    client = _StubClientWithExecutionTimeout()
    api_key: str | None = None
    timeout: float | None = None
    max_results: int | None = None
    name: str = "stub"
    description: str | None = None

    def get_swarm(self):  # noqa: ANN001
        return None


def test_agent_orchestrator_uses_execution_default_timeout_for_session_timeout() -> None:
    # Ensure session execution timeout uses execution.default_timeout_seconds, not
    # server.timeout_seconds.
    cfg = _Config(
        server=_ServerConfig(timeout_seconds=5.0),
        execution=_ExecutionConfig(default_timeout_seconds=123.0),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(_StubAgent(), logger=None)

    assert orchestrator.timeout == 123.0
    assert orchestrator.http_timeout == 5.0


def test_agent_orchestrator_http_timeout_does_not_affect_execution_timeout() -> None:
    """HTTP timeout and execution timeout are independent - http_timeout should not
    override execution timeout."""
    cfg = _Config(
        server=_ServerConfig(timeout_seconds=5.0),
        execution=_ExecutionConfig(default_timeout_seconds=123.0),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(_StubAgent(), http_timeout=30.0, logger=None)

    # http_timeout should be set from the parameter
    assert orchestrator.http_timeout == 30.0
    # execution timeout should NOT be affected by http_timeout - uses config default
    assert orchestrator.timeout == 123.0


def test_agent_orchestrator_uses_client_http_timeout_independently() -> None:
    """Client's HTTP timeout should not affect execution timeout."""
    cfg = _Config(
        server=_ServerConfig(timeout_seconds=5.0),
        execution=_ExecutionConfig(default_timeout_seconds=123.0),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(_StubAgentWithClientHttpTimeout(), logger=None)

    # http_timeout comes from client.timeout
    assert orchestrator.http_timeout == 17.0
    # execution timeout should use config default, NOT client.timeout
    assert orchestrator.timeout == 123.0


def test_agent_orchestrator_uses_client_execution_timeout() -> None:
    """Client's tool_execution_timeout should be used for execution timeout."""
    cfg = _Config(
        server=_ServerConfig(timeout_seconds=5.0),
        execution=_ExecutionConfig(default_timeout_seconds=123.0),
    )

    with patch(
        "maivn._internal.core.orchestrator.initialization.get_configuration",
        return_value=cfg,
    ):
        orchestrator = AgentOrchestrator(_StubAgentWithClientExecutionTimeout(), logger=None)

    # http_timeout comes from client.timeout
    assert orchestrator.http_timeout == 17.0
    # execution timeout should come from client.get_tool_execution_timeout()
    assert orchestrator.timeout == 300.0
