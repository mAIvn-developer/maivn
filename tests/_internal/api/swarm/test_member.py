# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

import pytest
from maivn_shared import BaseMessage, SessionResponse, SwarmConfig, create_uuid
from pydantic import JsonValue

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

JsonObject = dict[str, JsonValue]


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


def _tool_id_attr(obj: object) -> str:
    """Read the dynamic ``tool_id`` attached to callables by ``@toolify``."""
    tool_id = getattr(obj, "tool_id", None)
    assert isinstance(tool_id, str)
    return tool_id


def _agent_tool_spec_args(swarm: Swarm, agent_name: str) -> JsonObject:
    """Return the ``args_schema`` dict for the agent invocation tool."""
    tool = next(
        tool
        for tool in DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)
        if tool.name == agent_name
    )
    spec = ToolSpecFactory().create(agent_id=swarm.agents[0].id, tool=tool)
    return cast(JsonObject, spec.args_schema)


def _agent_tool_spec_metadata(swarm: Swarm, agent_name: str) -> JsonObject:
    """Return the ``metadata`` dict for the agent invocation tool."""
    tool = next(
        tool
        for tool in DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)
        if tool.name == agent_name
    )
    spec = ToolSpecFactory().create(agent_id=swarm.agents[0].id, tool=tool)
    assert spec.metadata is not None
    return spec.metadata


def _properties(args_schema: JsonObject) -> JsonObject:
    """Pull ``args_schema['properties']`` as a typed dict."""
    return cast(JsonObject, args_schema["properties"])


def _required(args_schema: JsonObject) -> list[JsonValue]:
    """Pull ``args_schema['required']`` as a typed list."""
    return cast("list[JsonValue]", args_schema["required"])


def _schema_field(properties: JsonObject, name: str) -> JsonObject:
    """Pull a single property schema as a typed dict."""
    return cast(JsonObject, properties[name])


def _control_entries(metadata: JsonObject, key: str) -> list[JsonObject]:
    """Pull ``execution_controls[key]`` (list of dicts) from a tool's metadata."""
    controls = cast(JsonObject, metadata["execution_controls"])
    return cast("list[JsonObject]", controls[key])


def _decorate_agent_with_member(swarm: Swarm) -> Agent:
    """Apply ``@swarm.member`` to an analyst factory and return the Agent."""

    @swarm.member
    def analyst() -> Agent:
        return _make_agent("analyst")

    # `swarm.member` (used as a decorator) collapses the union to Agent at runtime;
    # narrow for the test caller.
    assert isinstance(analyst, Agent)
    return analyst


def test_member_registers_agent_without_breaking_existing_registration() -> None:
    swarm = Swarm(name="swarm", agents=[_make_agent("existing")])

    analyst = _decorate_agent_with_member(swarm)

    assert [agent.name for agent in swarm.agents] == ["existing", "analyst"]
    assert analyst.get_swarm() is swarm


def test_member_builder_adds_tool_dependency_to_agent_invocation_schema() -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load account data")
    def load_account() -> dict[str, object]:
        return {"id": "acct-1"}

    _ = swarm.member.depends_on_tool(load_account, "account")(_make_agent("analyst"))

    args_schema = _agent_tool_spec_args(swarm, "analyst")
    account_schema = _schema_field(_properties(args_schema), "account")

    assert account_schema["type"] == "tool_dependency"
    assert account_schema["tool_id"] == _tool_id_attr(load_account)
    assert account_schema["tool_name"] == "load_account"
    assert "account" in _required(args_schema)


def test_member_decorator_order_can_store_pending_agent_dependencies() -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load profile")
    def load_profile() -> dict[str, object]:
        return {"name": "Ada"}

    @swarm.member
    @depends_on_tool(load_profile, "profile")
    def analyst() -> Agent:
        return _make_agent("analyst")

    assert isinstance(analyst, Agent)
    assert analyst.name is not None

    args_schema = _agent_tool_spec_args(swarm, analyst.name)
    profile_schema = _schema_field(_properties(args_schema), "profile")
    assert profile_schema["tool_id"] == _tool_id_attr(load_profile)


def test_member_agent_dependency_references_another_member_agent() -> None:
    researcher = _make_agent("researcher")
    writer = _make_agent("writer")
    swarm = Swarm(name="swarm", agents=[researcher])

    _ = swarm.member.depends_on_agent(researcher, "research")(writer)

    args_schema = _agent_tool_spec_args(swarm, "writer")
    research_schema = _schema_field(_properties(args_schema), "research")

    assert research_schema["type"] == "tool_dependency"
    assert research_schema["tool_id"] == create_uuid(f"agent_invoke_{researcher.id}")
    assert research_schema["tool_name"] == "researcher"
    assert research_schema["tool_type"] == "agent"


def test_member_execution_controls_use_generated_agent_tool_reference() -> None:
    researcher = _make_agent("researcher")
    writer = _make_agent("writer")
    swarm = Swarm(name="swarm", agents=[researcher])

    _ = swarm.member.depends_on_await_for(researcher).depends_on_reevaluate(researcher)(writer)

    metadata = _agent_tool_spec_metadata(swarm, "writer")
    await_for = _control_entries(metadata, "await_for")
    reevaluate = _control_entries(metadata, "reevaluate")

    assert await_for[0]["tool_id"] == create_uuid(f"agent_invoke_{researcher.id}")
    assert await_for[0]["tool_name"] == "researcher"
    assert reevaluate[0]["tool_name"] == "researcher"


def test_member_dependency_context_reaches_nested_agent_prompt_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load account data")
    def load_account() -> dict[str, object]:
        return {"id": "acct-1"}

    _analyst_obj = swarm.member.depends_on_tool(load_account, "account")(_make_agent("analyst"))
    assert isinstance(_analyst_obj, Agent)
    analyst = _analyst_obj

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    observed_messages: list[Sequence[BaseMessage]] = []
    observed_swarm_configs: list[SwarmConfig] = []

    class _Response:
        result: dict[str, object] = {"ok": True}
        response: str = "done"
        metadata: dict[str, object] = {}

    def _fake_invoke(self: Agent, **kwargs: object) -> _Response:
        assert self is analyst
        observed_messages.append(cast("Sequence[BaseMessage]", kwargs["messages"]))
        observed_swarm_configs.append(cast(SwarmConfig, kwargs["swarm_config"]))
        return _Response()

    # ``Agent.invoke`` returns ``SessionResponse`` in the protocol; the test fake stands in
    # for the contract via cast-through-object (Pattern 2) without sacrificing test intent.
    monkeypatch.setattr(
        Agent,
        "invoke",
        cast(object, _fake_invoke),
    )

    func = cast(object, tool.func)
    assert callable(func)
    _ = func(prompt="summarize", account={"id": "acct-1"})

    first_msg = observed_messages[0][0]
    content = cast(object, first_msg.content)
    assert isinstance(content, str)
    assert "Dependency context:" in content
    assert observed_swarm_configs[0].agent_dependency_context == {"account": {"id": "acct-1"}}
    assert observed_swarm_configs[0].agent_dependency_context_keys == ["account"]

    # Silence ``SessionResponse`` import not used at runtime (kept for future contract checks).
    _ = SessionResponse


def test_member_rejects_private_data_dependency_on_agents() -> None:
    agent = _make_agent("analyst")

    with pytest.raises(ValueError, match="depends_on_private_data is not supported"):
        # The decorator is typed for callables; passing an ``Agent`` is the runtime contract
        # we exercise here. cast-through-object (Pattern 2) keeps the test honest about the
        # type-narrowing we want the SDK to perform at runtime.
        target = cast("Callable[..., object]", cast(object, agent))
        _ = depends_on_private_data("account_id", "account_id")(target)


def test_member_rejects_self_agent_dependency() -> None:
    agent = _make_agent("analyst")
    swarm = Swarm(name="swarm")

    with pytest.raises(ValueError, match="cannot depend_on_agent themselves"):
        _ = swarm.member.depends_on_agent(agent, "self_result")(agent)


def test_dependency_decorator_can_attach_after_member_registration() -> None:
    swarm = Swarm(name="swarm")

    @swarm.toolify(description="Load data")
    def load_data() -> dict[str, object]:
        return {"ok": True}

    @swarm.member
    def analyst() -> Agent:
        return _make_agent("analyst")

    # ``@depends_on_tool`` expects a callable, but the SDK supports calling it directly on
    # the already-registered ``Agent`` (the member decorator returned the Agent at runtime).
    # cast-through-object lands the Agent past the ``F: Callable[..., object]`` bound.
    assert isinstance(analyst, Agent)
    target = cast("Callable[..., object]", cast(object, analyst))
    _ = depends_on_tool(load_data, "data")(target)

    assert analyst.name is not None
    args_schema = _agent_tool_spec_args(swarm, analyst.name)
    data_schema = _schema_field(_properties(args_schema), "data")
    assert data_schema["tool_id"] == _tool_id_attr(load_data)
