from __future__ import annotations

from typing import Any

import pytest
from maivn_shared import HumanMessage, SwarmConfig, create_uuid

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.application_services.state_compilation.dynamic_tool_factory import (
    DynamicToolFactory,
)
from maivn._internal.core.tool_specs.factory import ToolSpecFactory
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration
from maivn._internal.utils.decorators import (
    depends_on_private_data,
    depends_on_tool,
)


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def _make_agent(name: str) -> Agent:
    return Agent(name=name, client=_make_client())


def _agent_tool_spec(swarm: Swarm, agent_name: str):
    tool = next(
        tool
        for tool in DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)
        if tool.name == agent_name
    )
    return ToolSpecFactory().create(agent_id=swarm.agents[0].id, tool=tool)


def test_member_registers_agent_without_breaking_existing_registration() -> None:
    swarm = Swarm(name="swarm", agents=[_make_agent("existing")])

    @swarm.member
    def analyst() -> Agent:
        return _make_agent("analyst")

    assert [agent.name for agent in swarm.agents] == ["existing", "analyst"]
    assert analyst.get_swarm() is swarm


def test_member_builder_adds_tool_dependency_to_agent_invocation_schema() -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load account data")
    def load_account() -> dict[str, Any]:
        return {"id": "acct-1"}

    swarm.member.depends_on_tool(load_account, "account")(_make_agent("analyst"))

    spec = _agent_tool_spec(swarm, "analyst")
    account_schema = spec.args_schema["properties"]["account"]

    assert account_schema["type"] == "tool_dependency"
    assert account_schema["tool_id"] == load_account.tool_id
    assert account_schema["tool_name"] == "load_account"
    assert "account" in spec.args_schema["required"]


def test_member_decorator_order_can_store_pending_agent_dependencies() -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load profile")
    def load_profile() -> dict[str, Any]:
        return {"name": "Ada"}

    @swarm.member
    @depends_on_tool(load_profile, "profile")
    def analyst() -> Agent:
        return _make_agent("analyst")

    spec = _agent_tool_spec(swarm, analyst.name)

    assert spec.args_schema["properties"]["profile"]["tool_id"] == load_profile.tool_id


def test_member_agent_dependency_references_another_member_agent() -> None:
    researcher = _make_agent("researcher")
    writer = _make_agent("writer")
    swarm = Swarm(name="swarm", agents=[researcher])

    swarm.member.depends_on_agent(researcher, "research")(writer)

    spec = _agent_tool_spec(swarm, "writer")
    research_schema = spec.args_schema["properties"]["research"]

    assert research_schema["type"] == "tool_dependency"
    assert research_schema["tool_id"] == create_uuid(f"agent_invoke_{researcher.id}")
    assert research_schema["tool_name"] == "researcher"
    assert research_schema["tool_type"] == "agent"


def test_member_execution_controls_use_generated_agent_tool_reference() -> None:
    researcher = _make_agent("researcher")
    writer = _make_agent("writer")
    swarm = Swarm(name="swarm", agents=[researcher])

    swarm.member.depends_on_await_for(researcher).depends_on_reevaluate(researcher)(writer)

    spec = _agent_tool_spec(swarm, "writer")
    controls = spec.metadata["execution_controls"]

    assert controls["await_for"][0]["tool_id"] == create_uuid(f"agent_invoke_{researcher.id}")
    assert controls["await_for"][0]["tool_name"] == "researcher"
    assert controls["reevaluate"][0]["tool_name"] == "researcher"


def test_member_dependency_context_reaches_nested_agent_prompt_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load account data")
    def load_account() -> dict[str, Any]:
        return {"id": "acct-1"}

    analyst = swarm.member.depends_on_tool(load_account, "account")(_make_agent("analyst"))
    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    observed_messages: list[list[HumanMessage]] = []
    observed_swarm_configs: list[SwarmConfig] = []

    class _Response:
        result = {"ok": True}
        response = "done"
        metadata: dict[str, Any] = {}

    def _fake_invoke(self, **kwargs):  # noqa: ANN001
        assert self is analyst
        observed_messages.append(kwargs["messages"])
        observed_swarm_configs.append(kwargs["swarm_config"])
        return _Response()

    monkeypatch.setattr(Agent, "invoke", _fake_invoke)

    tool.func(prompt="summarize", account={"id": "acct-1"})

    assert "Dependency context:" in observed_messages[0][0].content
    assert observed_swarm_configs[0].agent_dependency_context == {"account": {"id": "acct-1"}}
    assert observed_swarm_configs[0].agent_dependency_context_keys == ["account"]


def test_member_rejects_private_data_dependency_on_agents() -> None:
    agent = _make_agent("analyst")

    with pytest.raises(ValueError, match="depends_on_private_data is not supported"):
        depends_on_private_data("account_id", "account_id")(agent)


def test_member_rejects_self_agent_dependency() -> None:
    agent = _make_agent("analyst")
    swarm = Swarm(name="swarm")

    with pytest.raises(ValueError, match="cannot depend_on_agent themselves"):
        swarm.member.depends_on_agent(agent, "self_result")(agent)


def test_dependency_decorator_can_attach_after_member_registration() -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load data")
    def load_data() -> dict[str, Any]:
        return {"ok": True}

    @depends_on_tool(load_data, "data")
    @swarm.member
    def analyst() -> Agent:
        return _make_agent("analyst")

    spec = _agent_tool_spec(swarm, analyst.name)

    assert spec.args_schema["properties"]["data"]["tool_id"] == load_data.tool_id
