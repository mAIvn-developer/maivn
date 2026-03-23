from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from maivn_shared import AgentDependency, HumanMessage

from maivn._internal.core.services.agent_execution_service import (
    AgentExecutionService,
    MockAgentExecutionService,
)


@dataclass
class _Response:
    result: Any = None
    metadata: dict[str, Any] | None = None
    messages: list[Any] | None = None

    def model_dump(self) -> dict[str, Any]:
        return {"fallback": True}


class _Agent:
    def __init__(self, response: Any, *, agent_id: str = "agent-1", name: str = "alpha") -> None:
        self.id = agent_id
        self.name = name
        self._response = response
        self.calls: list[list[HumanMessage]] = []

    def invoke(self, messages: list[HumanMessage]) -> Any:
        self.calls.append(messages)
        return self._response


class _NameOnlyRegistry:
    def __init__(self, agent: _Agent) -> None:
        self._agent = agent

    def get_agent(self, agent_id: str) -> None:
        return None

    def get_agent_by_name(self, name: str) -> _Agent | None:
        if name == self._agent.name:
            return self._agent
        return None


class _AgentsListRegistry:
    def __init__(self, agents: list[_Agent]) -> None:
        self.agents = agents


class _EmptyRegistry:
    agents: list[_Agent] = []

    def get_agent(self, agent_id: str) -> None:
        return None

    def get_agent_by_name(self, name: str) -> None:
        return None


class _MinimalRegistry:
    def get_agent(self, agent_id: str) -> None:
        return None


class _RaisingAgent:
    id = "agent-1"
    name = "alpha"

    def invoke(self, messages: list[HumanMessage]) -> Any:
        raise RuntimeError("boom")


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content


class _EmptyResponse:
    messages: list[Any] | None = None
    metadata: dict[str, Any] | None = None
    result: Any = None


def test_agent_execution_service_resolves_agent_by_name() -> None:
    agent = _Agent(_Response(result="by-name"))
    service = AgentExecutionService(agent_registry=_NameOnlyRegistry(agent))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id="alpha"),
        [HumanMessage(content="hello")],
    )

    assert result == "by-name"
    assert agent.calls[0][0].content == "hello"


def test_agent_execution_service_resolves_agent_from_agents_list() -> None:
    agent = _Agent(_Response(result="from-list"), agent_id="secondary")
    service = AgentExecutionService(agent_registry=_AgentsListRegistry([agent]))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id="secondary"),
        [],
    )

    assert result == "from-list"


def test_agent_execution_service_raises_for_unresolvable_agent() -> None:
    service = AgentExecutionService(agent_registry=_EmptyRegistry())

    with pytest.raises(ValueError, match="Cannot resolve agent dependency"):
        service.execute_agent_dependency(AgentDependency(arg_name="helper", agent_id="missing"), [])


def test_agent_execution_service_handles_registry_without_name_or_agents_access() -> None:
    service = AgentExecutionService(agent_registry=_MinimalRegistry())

    with pytest.raises(ValueError, match="Cannot resolve agent dependency"):
        service.execute_agent_dependency(AgentDependency(arg_name="helper", agent_id="missing"), [])


def test_agent_execution_service_extracts_result_from_messages() -> None:
    agent = _Agent(_Response(messages=[_Message("final-message")]))
    service = AgentExecutionService(agent_registry=_AgentsListRegistry([agent]))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id=agent.id),
        [],
    )

    assert result == "final-message"


def test_agent_execution_service_uses_model_dump_when_no_other_result_is_available() -> None:
    agent = _Agent(_Response())
    service = AgentExecutionService(agent_registry=_AgentsListRegistry([agent]))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id=agent.id),
        [],
    )

    assert result == {"fallback": True}


def test_agent_execution_service_returns_empty_dict_without_extractable_response() -> None:
    agent = _Agent(_EmptyResponse())
    service = AgentExecutionService(agent_registry=_AgentsListRegistry([agent]))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id=agent.id),
        [],
    )

    assert result == {}


def test_agent_execution_service_wraps_agent_invoke_failures() -> None:
    service = AgentExecutionService(agent_registry=_AgentsListRegistry([_RaisingAgent()]))

    with pytest.raises(ValueError, match="Agent execution failed"):
        service.execute_agent_dependency(AgentDependency(arg_name="helper", agent_id="agent-1"), [])


def test_agent_execution_service_accepts_late_registry_binding() -> None:
    agent = _Agent(_Response(result="late-bind"))
    service = AgentExecutionService()
    service.set_agent_registry(_AgentsListRegistry([agent]))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id=agent.id),
        [],
    )

    assert result == "late-bind"


def test_mock_agent_execution_service_returns_default_response_pattern() -> None:
    service = MockAgentExecutionService()

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id="unknown-agent"),
        [],
    )

    assert result == "mock_response_for_unknown-agent"
