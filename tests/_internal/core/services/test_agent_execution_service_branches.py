# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

import pytest
from maivn_shared import AgentDependency, BaseMessage, HumanMessage

from maivn._internal.core.services.agent_execution_service import (
    AgentExecutionService,
    AgentExecutionTarget,
    AgentRegistry,
    MockAgentExecutionService,
)


@dataclass
class _Message:
    content: object


@dataclass
class _Response:
    result: object | None = None
    metadata: dict[str, object] | None = None
    messages: list[_Message] | None = None

    def model_dump(self) -> dict[str, object]:
        return {"fallback": True}


@dataclass
class _Agent:
    response: object
    id: str = "agent-1"
    name: str | None = "alpha"
    calls: list[Sequence[BaseMessage]] = field(default_factory=list)

    def invoke(self, messages: Sequence[BaseMessage]) -> object:
        self.calls.append(list(messages))
        return self.response


@dataclass
class _NameOnlyRegistry:
    """Lookup registry that only resolves agents by name."""

    agent: AgentExecutionTarget

    def get_agent(self, agent_id: str) -> AgentExecutionTarget | None:
        _ = agent_id
        return None

    def get_agent_by_name(self, name: str) -> AgentExecutionTarget | None:
        if name == self.agent.name:
            return self.agent
        return None


@dataclass
class _AgentsListRegistry:
    """Sequence registry exposing agents directly."""

    agents: Sequence[AgentExecutionTarget]


class _EmptyRegistry:
    """Lookup registry whose lookups all return ``None``."""

    def get_agent(self, agent_id: str) -> AgentExecutionTarget | None:
        _ = agent_id
        return None

    def get_agent_by_name(self, name: str) -> AgentExecutionTarget | None:
        _ = name
        return None


class _MinimalRegistry:
    """Registry whose surface only includes ``get_agent``.

    Exercises the no-protocol-match branch.
    """

    def get_agent(self, agent_id: str) -> AgentExecutionTarget | None:
        _ = agent_id
        return None


class _RaisingAgent:
    id: str = "agent-1"
    name: str | None = "alpha"

    def invoke(self, messages: Sequence[BaseMessage]) -> object:
        _ = messages
        raise RuntimeError("boom")


class _EmptyResponse:
    messages: list[_Message] | None = None
    metadata: dict[str, object] | None = None
    result: object | None = None


def test_agent_execution_service_resolves_agent_by_name() -> None:
    agent = _Agent(_Response(result="by-name"))
    service = AgentExecutionService(agent_registry=_NameOnlyRegistry(agent))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id="alpha"),
        [HumanMessage(content="hello")],
    )

    assert result == "by-name"
    first_message = agent.calls[0][0]
    # BaseMessage.content is typed as str | list[...]; the cast keeps the equality typed.
    assert cast(str, first_message.content) == "hello"


def test_agent_execution_service_resolves_agent_from_agents_list() -> None:
    agent = _Agent(_Response(result="from-list"), id="secondary")
    service = AgentExecutionService(agent_registry=_AgentsListRegistry([agent]))

    result = service.execute_agent_dependency(
        AgentDependency(arg_name="helper", agent_id="secondary"),
        [],
    )

    assert result == "from-list"


def test_agent_execution_service_raises_for_unresolvable_agent() -> None:
    service = AgentExecutionService(agent_registry=_EmptyRegistry())

    with pytest.raises(ValueError, match="Cannot resolve agent dependency"):
        _ = service.execute_agent_dependency(
            AgentDependency(arg_name="helper", agent_id="missing"), []
        )


def test_agent_execution_service_handles_registry_without_name_or_agents_access() -> None:
    # The minimal registry deliberately fails both AgentLookupRegistry and
    # AgentSequenceRegistry protocol checks at runtime — cast through object to
    # bypass static checking while exercising that no-match resolution branch.
    service = AgentExecutionService(
        agent_registry=cast(AgentRegistry, cast(object, _MinimalRegistry())),
    )

    with pytest.raises(ValueError, match="Cannot resolve agent dependency"):
        _ = service.execute_agent_dependency(
            AgentDependency(arg_name="helper", agent_id="missing"), []
        )


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
        _ = service.execute_agent_dependency(
            AgentDependency(arg_name="helper", agent_id="agent-1"), []
        )


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
