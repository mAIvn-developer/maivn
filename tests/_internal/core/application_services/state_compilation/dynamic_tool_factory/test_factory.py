# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import cast

import pytest
from maivn_shared import FINAL_EVENT_NAME, UPDATE_EVENT_NAME, AgentDependency
from maivn_shared.domain.entities.memory_config import MemoryConfig
from pydantic import JsonValue

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.application_services.state_compilation.dynamic_tool_factory import (
    DynamicToolFactory,
)
from maivn._internal.core.entities import FunctionTool, SSEEvent
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration
from maivn._internal.utils.reporting.context import (
    allow_nested_response_stream,
    current_sdk_delivery_mode,
    current_stream_event_forwarder,
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
        result: dict[str, object] = {"value": {"value": 5}}
        responses: list[str] = ["text"]
        metadata: dict[str, object] = {"detailed_token_usage": {"total": 1}}

    # Access protected helper via typed getattr to keep the test focused on
    # the mixin's behavior without leaking the underscore-name to type-check.
    extract = cast("Callable[..., object]", factory.extract_agent_response)
    payload_obj = extract(_Response(), agent_id="agent", include_response=True)
    payload = cast(dict[str, object], payload_obj)

    assert payload["result"] == 5
    assert payload["response"] == "text"
    assert payload["detailed_token_usage"] == {"total": 1}


def test_dynamic_tool_factory_unwraps_nested_results() -> None:
    factory = DynamicToolFactory()

    nested: dict[str, object] = {"value": {"value": 1}}
    unwrap = cast("Callable[..., object]", factory.unwrap_agent_result)
    assert unwrap(nested) == 1


def test_dynamic_tool_invocation_sets_nested_stream_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(
        name="alpha",
        memory_config=MemoryConfig(level="none"),
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
        memory_config=MemoryConfig(level="glimpse"),
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
        result: dict[str, object] = {"ok": True}
        response: str = "done"
        metadata: dict[str, object] = {}

    def _fake_invoke(self: Agent, **kwargs: object) -> _Response:
        del self  # not used by the stub
        observed.append(allow_nested_response_stream.get())
        swarm_config = kwargs.get("swarm_config")
        memory_assets_config = kwargs.get("memory_assets_config")
        memory_config = kwargs.get("memory_config")
        if swarm_config is not None:
            nested_modes.append(
                cast(bool | str | None, getattr(swarm_config, "included_nested_synthesis", None))
            )
            delivery_mode = cast(object, getattr(swarm_config, "sdk_delivery_mode", None))
            nested_delivery_modes.append(delivery_mode if isinstance(delivery_mode, str) else None)
            nested_memory_levels.append(
                cast(bool | str | None, getattr(memory_config, "level", None))
            )
            skill_payloads = cast(object, getattr(memory_assets_config, "defined_skills", None))
            if isinstance(skill_payloads, list):
                payload_items = cast(list[object], skill_payloads)
                collected: list[str] = []
                for item in payload_items:
                    skill_id = cast(object, getattr(item, "skill_id", None))
                    if isinstance(skill_id, str):
                        collected.append(skill_id)
                nested_skill_ids.append(collected)
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
    _ = tool.func(
        prompt="a",
        use_as_final_output=False,
        force_final_tool=False,
        model=None,
        included_nested_synthesis=True,
    )
    _ = tool.func(prompt="b", use_as_final_output=True, force_final_tool=False, model=None)
    assert allow_nested_response_stream.get() is False
    assert observed == [False, False]
    assert nested_modes == [True, "auto"]
    assert nested_delivery_modes == ["invoke", "invoke"]
    assert nested_memory_levels == ["glimpse", "glimpse"]
    assert nested_skill_ids == [
        ["agent-skill", "swarm-skill"],
        ["agent-skill", "swarm-skill"],
    ]


def test_dynamic_tool_invocation_suppresses_nested_streaming_for_non_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    observed: list[bool] = []

    class _Response:
        result: dict[str, JsonValue] = {"ok": True}
        response: str = "done"
        metadata: dict[str, object] = {}

    def _fake_stream(self: Agent, **kwargs: object) -> Iterator[SSEEvent]:
        del self, kwargs  # not used by the stub
        observed.append(allow_nested_response_stream.get())
        payload: dict[str, JsonValue] = {
            "session_id": "nested-session",
            "status": "completed",
            "result": _Response.result,
            "responses": [_Response.response],
        }
        yield SSEEvent(
            name=FINAL_EVENT_NAME,
            payload=payload,
        )

    monkeypatch.setattr(Agent, "stream", _fake_stream)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    token = current_sdk_delivery_mode.set("stream")
    try:
        _ = tool.func(prompt="stream this")
    finally:
        current_sdk_delivery_mode.reset(token)

    assert observed == [False]


def test_dynamic_tool_invocation_enables_nested_streaming_for_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    observed: list[bool] = []

    class _Response:
        result: dict[str, JsonValue] = {"ok": True}
        response: str = "done"
        metadata: dict[str, object] = {}

    def _fake_stream(self: Agent, **kwargs: object) -> Iterator[SSEEvent]:
        del self, kwargs  # not used by the stub
        observed.append(allow_nested_response_stream.get())
        payload: dict[str, JsonValue] = {
            "session_id": "nested-session",
            "status": "completed",
            "result": _Response.result,
            "responses": [_Response.response],
        }
        yield SSEEvent(
            name=FINAL_EVENT_NAME,
            payload=payload,
        )

    monkeypatch.setattr(Agent, "stream", _fake_stream)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    token = current_sdk_delivery_mode.set("stream")
    try:
        _ = tool.func(prompt="stream this", use_as_final_output=True)
    finally:
        current_sdk_delivery_mode.reset(token)

    assert observed == [True]


def test_dynamic_tool_invocation_uses_agent_stream_for_stream_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    invoked: list[bool] = []
    streamed: list[bool] = []

    class _Response:
        result: dict[str, object] = {"invoke": True}
        response: str = "invoke"
        metadata: dict[str, object] = {}

    def _fake_invoke(self: Agent, **kwargs: object) -> _Response:
        del self, kwargs
        invoked.append(True)
        return _Response()

    def _fake_stream(self: Agent, **kwargs: object) -> Iterator[SSEEvent]:
        del self, kwargs
        streamed.append(allow_nested_response_stream.get())
        payload: dict[str, JsonValue] = {
            "session_id": "nested-session",
            "status": "completed",
            "result": {"ok": True},
            "responses": ["streamed"],
        }
        yield SSEEvent(
            name=FINAL_EVENT_NAME,
            payload=payload,
        )

    monkeypatch.setattr(Agent, "invoke", _fake_invoke)
    monkeypatch.setattr(Agent, "stream", _fake_stream)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    token = current_sdk_delivery_mode.set("stream")
    try:
        result = tool.func(prompt="stream this")
    finally:
        current_sdk_delivery_mode.reset(token)

    assert invoked == []
    assert streamed == [False]
    assert result == {"result": {"ok": True}, "response": "streamed"}


def test_dynamic_tool_invocation_suppresses_non_final_nested_streaming_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])
    forwarded: list[SSEEvent] = []

    def _fake_stream(self: Agent, **kwargs: object) -> Iterator[SSEEvent]:
        del self, kwargs
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "child-assistant",
                "streaming_content": "Hel",
            },
        )
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "child-assistant",
                "streaming_content": "Hello",
            },
        )
        yield SSEEvent(
            name=FINAL_EVENT_NAME,
            payload={
                "session_id": "nested-session",
                "status": "completed",
                "result": {"ok": True},
                "responses": ["Hello"],
            },
        )

    monkeypatch.setattr(Agent, "stream", _fake_stream)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    mode_token = current_sdk_delivery_mode.set("stream")
    forward_token = current_stream_event_forwarder.set(forwarded.append)
    try:
        result = tool.func(prompt="stream this")
    finally:
        current_stream_event_forwarder.reset(forward_token)
        current_sdk_delivery_mode.reset(mode_token)

    assert result == {"result": {"ok": True}, "response": "Hello"}
    assert forwarded == []


def test_dynamic_tool_invocation_suppresses_non_response_final_output_streaming_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])
    forwarded: list[SSEEvent] = []

    def _fake_stream(self: Agent, **kwargs: object) -> Iterator[SSEEvent]:
        del self, kwargs
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "child-assistant",
                "streaming_content": "Hel",
            },
        )
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "child-assistant",
                "streaming_content": "Hello",
            },
        )
        yield SSEEvent(
            name=FINAL_EVENT_NAME,
            payload={
                "session_id": "nested-session",
                "status": "completed",
                "result": {"ok": True},
                "responses": ["Hello"],
            },
        )

    monkeypatch.setattr(Agent, "stream", _fake_stream)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    mode_token = current_sdk_delivery_mode.set("stream")
    forward_token = current_stream_event_forwarder.set(forwarded.append)
    try:
        result = tool.func(prompt="stream this", use_as_final_output=True)
    finally:
        current_stream_event_forwarder.reset(forward_token)
        current_sdk_delivery_mode.reset(mode_token)

    assert result == {
        "result": {"ok": True},
        "response": "Hello",
    }
    assert forwarded == []


def test_dynamic_tool_invocation_forwards_chat_agent_final_output_streaming_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])
    forwarded: list[SSEEvent] = []

    def _fake_stream(self: Agent, **kwargs: object) -> Iterator[SSEEvent]:
        del self, kwargs
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "child-assistant",
                "streaming_content": "Internal draft",
            },
        )
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "chat_agent",
                "streaming_content": "Hel",
            },
        )
        yield SSEEvent(
            name=UPDATE_EVENT_NAME,
            payload={
                "assistant_id": "chat_agent",
                "streaming_content": "Hello",
            },
        )
        yield SSEEvent(
            name=FINAL_EVENT_NAME,
            payload={
                "session_id": "nested-session",
                "status": "completed",
                "result": {"ok": True},
                "responses": ["Hello"],
            },
        )

    monkeypatch.setattr(Agent, "stream", _fake_stream)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    mode_token = current_sdk_delivery_mode.set("stream")
    forward_token = current_stream_event_forwarder.set(forwarded.append)
    try:
        result = tool.func(prompt="stream this", use_as_final_output=True)
    finally:
        current_stream_event_forwarder.reset(forward_token)
        current_sdk_delivery_mode.reset(mode_token)

    assert result == {
        "result": {"ok": True},
        "response": "Hello",
        "response_streamed_live": True,
    }
    assert [event.name for event in forwarded] == [UPDATE_EVENT_NAME, UPDATE_EVENT_NAME]
    assert [
        cast(dict[str, object], event.payload).get("streaming_content") for event in forwarded
    ] == ["Hel", "Hello"]


def test_forward_nested_streaming_update_respects_nested_stream_flag() -> None:
    forwarded: list[SSEEvent] = []
    event = SSEEvent(
        name=UPDATE_EVENT_NAME,
        payload={
            "assistant_id": "child-assistant",
            "streaming_content": "Hello",
        },
    )
    chat_event = SSEEvent(
        name=UPDATE_EVENT_NAME,
        payload={
            "assistant_id": "chat_agent",
            "streaming_content": "Hello",
        },
    )
    forward = cast(
        "Callable[[object], bool]",
        object.__getattribute__(DynamicToolFactory, "_forward_nested_streaming_update"),
    )

    forward_token = current_stream_event_forwarder.set(forwarded.append)
    blocked_token = allow_nested_response_stream.set(False)
    try:
        assert forward(event) is False
    finally:
        allow_nested_response_stream.reset(blocked_token)
        current_stream_event_forwarder.reset(forward_token)
    assert forwarded == []

    forward_token = current_stream_event_forwarder.set(forwarded.append)
    allowed_token = allow_nested_response_stream.set(True)
    try:
        assert forward(event) is False
        assert forward(chat_event) is True
    finally:
        allow_nested_response_stream.reset(allowed_token)
        current_stream_event_forwarder.reset(forward_token)

    assert forwarded == [chat_event]


def test_dynamic_tool_invocation_propagates_memory_recall_turn_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_a = Agent(name="alpha", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent_a])

    observed_recall_flags: list[object] = []

    class _Response:
        result: dict[str, object] = {"ok": True}
        response: str = "done"
        metadata: dict[str, object] = {}

    def _fake_invoke(self: Agent, **kwargs: object) -> _Response:
        del self  # not used by the stub
        memory_assets_config = kwargs.get("memory_assets_config")
        observed_recall_flags.append(
            cast(object, getattr(memory_assets_config, "recall_turn_active", None))
        )
        return _Response()

    monkeypatch.setattr(Agent, "invoke", _fake_invoke)

    tool = DynamicToolFactory().create_swarm_agent_invocation_tools(swarm)[0]
    _ = tool.func(prompt="analyze", memory_recall_turn_active=True)

    assert observed_recall_flags == [True]
