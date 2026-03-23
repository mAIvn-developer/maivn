from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from maivn_shared import AgentDependency, HumanMessage

import maivn._internal.core.services.agent_execution_service as agent_execution_service_module
from maivn._internal.core.services.agent_execution_service import (
    AgentExecutionService,
    MockAgentExecutionService,
)


@dataclass
class _Response:
    result: dict | None = None
    metadata: dict | None = None
    messages: list | None = None

    def model_dump(self) -> dict:
        return {"fallback": True}


@dataclass
class _Swarm:
    name: str


class _Agent:
    def __init__(
        self,
        agent_id: str,
        name: str,
        response: _Response,
        swarm_name: str | None = None,
    ) -> None:
        self.id = agent_id
        self.name = name
        self._response = response
        self._swarm = _Swarm(swarm_name) if swarm_name else None

    def invoke(self, messages):
        return self._response

    def get_swarm(self):
        return self._swarm


class _Registry:
    def __init__(self, agent: _Agent) -> None:
        self._agent = agent
        self.agents = [agent]

    def get_agent(self, agent_id: str):
        if agent_id == self._agent.id:
            return self._agent
        return None

    def get_agent_by_name(self, name: str):
        if name == self._agent.name:
            return self._agent
        return None


class _Reporter:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.completions: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []

    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        self.starts.append(
            {
                "tool_name": tool_name,
                "event_id": event_id,
                "tool_type": tool_type,
                "agent_name": agent_name,
                "tool_args": tool_args,
                "swarm_name": swarm_name,
            }
        )

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
    ) -> None:
        self.completions.append(
            {
                "event_id": event_id,
                "elapsed_ms": elapsed_ms,
                "result": result,
            }
        )

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        self.errors.append(
            {
                "tool_name": tool_name,
                "error": error,
                "event_id": event_id,
                "elapsed_ms": elapsed_ms,
            }
        )


def test_agent_execution_service_resolves_and_executes() -> None:
    response = _Response(result={"ok": True})
    agent = _Agent("agent-1", "alpha", response)
    registry = _Registry(agent)

    service = AgentExecutionService(agent_registry=registry)
    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")

    result = service.execute_agent_dependency(dependency, [HumanMessage(content="hi")])

    assert result == {"ok": True}


def test_agent_execution_service_falls_back_to_metadata() -> None:
    response = _Response(result=None, metadata={"result": "meta"})
    agent = _Agent("agent-1", "alpha", response)
    registry = _Registry(agent)

    service = AgentExecutionService(agent_registry=registry)
    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")

    result = service.execute_agent_dependency(dependency, [])

    assert result == "meta"


def test_agent_execution_service_reports_dependency_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(result={"ok": True})
    agent = _Agent("agent-1", "alpha", response)
    registry = _Registry(agent)
    reporter = _Reporter()

    monkeypatch.setattr(agent_execution_service_module, "get_current_reporter", lambda: reporter)

    service = AgentExecutionService(agent_registry=registry)
    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")

    result = service.execute_agent_dependency(dependency, [HumanMessage(content="hi")])

    assert result == {"ok": True}
    assert len(reporter.starts) == 1
    assert reporter.starts[0]["tool_name"] == "alpha"
    assert reporter.starts[0]["tool_type"] == "agent"
    assert reporter.starts[0]["agent_name"] == "alpha"
    assert reporter.starts[0]["tool_args"] == {"agent_id": "agent-1"}
    assert len(reporter.completions) == 1
    assert reporter.completions[0]["event_id"] == reporter.starts[0]["event_id"]
    assert reporter.completions[0]["result"] == {"ok": True}
    assert reporter.errors == []


def test_agent_execution_service_reports_dependency_swarm_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(result={"ok": True})
    agent = _Agent("agent-1", "alpha", response, swarm_name="research")
    registry = _Registry(agent)
    reporter = _Reporter()

    monkeypatch.setattr(agent_execution_service_module, "get_current_reporter", lambda: reporter)

    service = AgentExecutionService(agent_registry=registry)
    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")

    result = service.execute_agent_dependency(dependency, [HumanMessage(content="hi")])

    assert result == {"ok": True}
    assert len(reporter.starts) == 1
    assert reporter.starts[0]["agent_name"] == "alpha"
    assert reporter.starts[0]["swarm_name"] == "research"


def test_agent_execution_service_reports_dependency_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RaisingAgent:
        id = "agent-1"
        name = "alpha"

        def invoke(self, messages):
            _ = messages
            raise RuntimeError("boom")

    reporter = _Reporter()
    registry = _Registry(_RaisingAgent())

    monkeypatch.setattr(agent_execution_service_module, "get_current_reporter", lambda: reporter)

    service = AgentExecutionService(agent_registry=registry)

    with pytest.raises(ValueError, match="Agent execution failed"):
        service.execute_agent_dependency(AgentDependency(arg_name="agent", agent_id="agent-1"), [])

    assert len(reporter.starts) == 1
    assert reporter.completions == []
    assert len(reporter.errors) == 1
    assert reporter.errors[0]["tool_name"] == "alpha"
    assert reporter.errors[0]["event_id"] == reporter.starts[0]["event_id"]
    assert reporter.errors[0]["error"] == "boom"


def test_agent_execution_service_raises_without_registry() -> None:
    service = AgentExecutionService()
    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")

    with pytest.raises(ValueError):
        service.execute_agent_dependency(dependency, [])


def test_mock_agent_execution_service_returns_mock() -> None:
    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")
    service = MockAgentExecutionService(mock_responses={"agent-1": "value"})

    assert service.execute_agent_dependency(dependency, []) == "value"

    service.add_mock_response("agent-2", "value-2")
    dependency_two = AgentDependency(arg_name="agent", agent_id="agent-2")
    assert service.execute_agent_dependency(dependency_two, []) == "value-2"
