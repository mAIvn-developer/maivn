from __future__ import annotations

from maivn_shared import AgentDependency

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.application_services.state_compilation.dynamic_tool_factory import (
    DynamicToolFactory,
)
from maivn._internal.core.entities import FunctionTool
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration
from maivn._internal.utils.reporting.context import (
    allow_nested_response_stream,
    current_sdk_delivery_mode,
)


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def test_dynamic_tool_factory_creates_agent_tools() -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    agent_b = Agent(name="beta", client=_make_client())

    swarm = Swarm(name="swarm", agents=[agent_a, agent_b])

    tool = FunctionTool(
        name="tool",
        description="tool",
        tool_id="tool",
        func=lambda: "ok",
        dependencies=[AgentDependency(arg_name="agent", agent_id=agent_b.id)],
    )

    factory = DynamicToolFactory()
    agent_tools, user_tools = factory.create_dependency_tools([tool], swarm)

    assert agent_tools
    assert user_tools == []


def test_dynamic_tool_factory_creates_swarm_invocation_tools() -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    factory = DynamicToolFactory()
    tools = factory.create_swarm_agent_invocation_tools(swarm)

    assert len(tools) == 1
    assert tools[0].name == agent_a.name


def test_dynamic_tool_factory_extracts_agent_response() -> None:
    factory = DynamicToolFactory()

    class _Response:
        def __init__(self) -> None:
            self.result = {"value": {"value": 5}}
            self.responses = ["text"]
            self.metadata = {"detailed_token_usage": {"total": 1}}

    payload = factory._extract_agent_response(_Response(), agent_id="agent", include_response=True)

    assert payload["result"] == 5
    assert payload["response"] == "text"
    assert payload["detailed_token_usage"] == {"total": 1}


def test_dynamic_tool_factory_unwraps_nested_results() -> None:
    factory = DynamicToolFactory()

    nested = {"value": {"value": 1}}
    assert factory._unwrap_agent_result(nested) == 1


def test_dynamic_tool_invocation_sets_nested_stream_context(monkeypatch) -> None:
    agent_a = Agent(
        name="alpha",
        memory_config={"level": "none"},
        skills=[
            {
                "skill_id": "agent-skill",
                "name": "agent_deploy_pattern",
                "description": "Agent-specific deploy flow.",
                "steps": [{"action": "deploy", "tool": "deploy_service"}],
            }
        ],
        client=_make_client(),
    )
    swarm = Swarm(
        name="swarm",
        agents=[agent_a],
        memory_config={"level": "glimpse"},
        skills=[
            {
                "skill_id": "swarm-skill",
                "name": "swarm_release_gate",
                "description": "Swarm-level release validation flow.",
                "steps": [{"action": "validate", "tool": "run_health_checks"}],
            }
        ],
    )

    observed: list[bool] = []
    nested_modes: list[bool | str | None] = []
    nested_delivery_modes: list[str | None] = []
    nested_memory_levels: list[bool | str | None] = []
    nested_skill_ids: list[list[str]] = []

    class _Response:
        result = {"ok": True}
        response = "done"
        metadata: dict[str, object] = {}

    def _fake_invoke(self, **kwargs):  # noqa: ANN001
        _ = (self, kwargs)
        observed.append(allow_nested_response_stream.get())
        metadata = kwargs.get("metadata") if isinstance(kwargs, dict) else None
        memory_config = kwargs.get("memory_config") if isinstance(kwargs, dict) else None
        if isinstance(metadata, dict):
            nested_modes.append(metadata.get("swarm_included_nested_synthesis"))
            delivery_mode = metadata.get("maivn_sdk_delivery_mode")
            nested_delivery_modes.append(delivery_mode if isinstance(delivery_mode, str) else None)
            nested_memory_levels.append(getattr(memory_config, "level", None))
            skill_payloads = metadata.get("memory_defined_skills")
            if isinstance(skill_payloads, list):
                nested_skill_ids.append(
                    [
                        str(item.get("skill_id"))
                        for item in skill_payloads
                        if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
                    ]
                )
            else:
                nested_skill_ids.append([])
        else:
            nested_modes.append(None)
            nested_delivery_modes.append(None)
            nested_memory_levels.append(None)
            nested_skill_ids.append([])
        return _Response()

    monkeypatch.setattr(Agent, "invoke", _fake_invoke)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]

    assert allow_nested_response_stream.get() is False
    tool.func(
        prompt="a",
        use_as_final_output=False,
        force_final_tool=False,
        model=None,
        included_nested_synthesis=True,
    )
    tool.func(prompt="b", use_as_final_output=True, force_final_tool=False, model=None)
    assert allow_nested_response_stream.get() is False
    assert observed == [False, False]
    assert nested_modes == [True, "auto"]
    assert nested_delivery_modes == ["invoke", "invoke"]
    assert nested_memory_levels == ["glimpse", "glimpse"]
    assert nested_skill_ids == [
        ["agent-skill", "swarm-skill"],
        ["agent-skill", "swarm-skill"],
    ]


def test_dynamic_tool_invocation_enables_nested_streaming_for_stream_mode(monkeypatch) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    observed: list[bool] = []

    class _Response:
        result = {"ok": True}
        response = "done"
        metadata: dict[str, object] = {}

    def _fake_invoke(self, **kwargs):  # noqa: ANN001
        _ = (self, kwargs)
        observed.append(allow_nested_response_stream.get())
        return _Response()

    monkeypatch.setattr(Agent, "invoke", _fake_invoke)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    token = current_sdk_delivery_mode.set("stream")
    try:
        tool.func(prompt="stream this")
    finally:
        current_sdk_delivery_mode.reset(token)

    assert observed == [True]


def test_dynamic_tool_invocation_propagates_memory_recall_turn_active(monkeypatch) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    observed_recall_flags: list[object] = []

    class _Response:
        result = {"ok": True}
        response = "done"
        metadata: dict[str, object] = {}

    def _fake_invoke(self, **kwargs):  # noqa: ANN001
        _ = self
        metadata = kwargs.get("metadata")
        if isinstance(metadata, dict):
            observed_recall_flags.append(metadata.get("memory_recall_turn_active"))
        else:
            observed_recall_flags.append(None)
        return _Response()

    monkeypatch.setattr(Agent, "invoke", _fake_invoke)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    tool.func(prompt="analyze", memory_recall_turn_active=True)

    assert observed_recall_flags == [True]
