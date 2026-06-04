# pyright: strict
from __future__ import annotations

import base64
import sys
from collections.abc import Callable, Iterator, Sequence
from typing import Literal, cast

import pytest
from maivn_shared import (
    FINAL_EVENT_NAME,
    BaseMessage,
    HumanMessage,
    MemoryConfig,
    MemoryInsightExtractionConfig,
    MemorySkillExtractionConfig,
    SessionOrchestrationConfig,
    SessionRequest,
    SystemMessage,
)
from pydantic import JsonValue, ValidationError

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.entities import FunctionTool, SSEEvent
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration

JsonObject = dict[str, JsonValue]


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


# MARK: - Private accessor helpers
#
# The SDK keeps several helpers underscore-prefixed; these wrappers let tests
# exercise the contract via ``cast(Callable[...], getattr(...))`` (Pattern 2)
# rather than reaching into protected members directly.


def _set_client_timezone(client: Client, timezone: str) -> None:
    timezone_attr = "_client_timezone"
    setattr(client, timezone_attr, timezone)


def _swarm_prepare_messages(swarm: Swarm, messages: list[BaseMessage]) -> list[BaseMessage]:
    prepare_messages_attr = "_prepare_messages"
    fn = cast(
        "Callable[[list[BaseMessage]], list[BaseMessage]]",
        getattr(swarm, prepare_messages_attr),
    )
    return fn(messages)


def _swarm_build_invocation_tool_map(swarm: Swarm) -> dict[str, str]:
    tool_map_attr = "_build_invocation_tool_map"
    fn = cast("Callable[[], dict[str, str]]", getattr(swarm, tool_map_attr))
    return fn()


def _swarm_build_agent_roster_entry(
    swarm: Swarm, agent: Agent, tool_map: dict[str, str]
) -> JsonObject:
    roster_entry_attr = "_build_agent_roster_entry"
    fn = cast(
        "Callable[[Agent, dict[str, str]], JsonObject]",
        getattr(swarm, roster_entry_attr),
    )
    return fn(agent, tool_map)


def _swarm_enrich_state_metadata(swarm: Swarm, state: SessionRequest) -> None:
    enrich_metadata_attr = "_enrich_state_metadata"
    fn = cast("Callable[[SessionRequest], None]", getattr(swarm, enrich_metadata_attr))
    fn(state)


def _swarm_validate_force_final_tool_request(swarm: Swarm, force: bool) -> None:
    validate_final_tool_attr = "_validate_force_final_tool_request"
    fn = cast("Callable[[bool], None]", getattr(swarm, validate_final_tool_attr))
    fn(force)


def _swarm_add_tool(swarm: Swarm, tool: FunctionTool) -> None:
    """Add a tool to the swarm via its internal ``_tool_repo`` PrivateAttr."""
    tool_repo_attr = "_tool_repo"
    repo: object = getattr(swarm, tool_repo_attr, None)
    add_tool = cast("Callable[[FunctionTool], None]", getattr(repo, "add_tool", None))
    add_tool(tool)


def _agent_add_tool(agent: Agent, tool: FunctionTool) -> None:
    tool_repo_attr = "_tool_repo"
    repo: object = getattr(agent, tool_repo_attr, None)
    add_tool = cast("Callable[[FunctionTool], None]", getattr(repo, "add_tool", None))
    add_tool(tool)


def _override_swarm_build_orchestrator(swarm: Swarm, factory: Callable[[Agent], object]) -> None:
    """Install a test-stub orchestrator factory on ``swarm``.

    The orchestrator protocol is wide; tests provide focused stubs that satisfy
    only the methods exercised. The ``object`` channel mirrors Pattern 2.
    """
    build_orchestrator_attr = "_build_orchestrator"
    setattr(swarm, build_orchestrator_attr, factory)


def _content_text(message: BaseMessage) -> str:
    """``BaseMessage.content`` is union-typed by the upstream stub; coerce to ``str``."""
    raw = cast(object, message.content)
    return raw if isinstance(raw, str) else str(raw)


def _bound_resource_content_base64(resource: object) -> str | None:
    """Pull ``content_base64`` from a bound resource without leaking protocol types."""
    value = cast("str | None", getattr(resource, "content_base64", None))
    return value


# MARK: - Tests


def test_agent_compile_state_uses_execution_config_for_timezone_context() -> None:
    client = _make_client()
    _set_client_timezone(client, "America/Chicago")
    agent = Agent(name="agent", client=client)

    state = agent.compile_state([HumanMessage(content="hello")])

    assert state.execution_config is not None
    assert state.execution_config.client_timezone == "America/Chicago"
    assert state.execution_config.sdk_deployment_timezone == "UTC"
    assert "client_timezone" not in (state.metadata or {})


def test_agent_compile_state_uses_orchestration_config_for_loop_controls() -> None:
    agent = Agent(
        name="agent",
        client=_make_client(),
        orchestration_config=SessionOrchestrationConfig(allow_reevaluate_loop=True),
    )

    state = agent.compile_state(
        [HumanMessage(content="hello")],
        orchestration_config={"max_cycles": 2},
    )

    assert state.orchestration_config is not None
    assert state.orchestration_config.allow_reevaluate_loop is True
    assert state.orchestration_config.max_cycles == 2
    assert "allow_reevaluate_loop" not in (state.metadata or {})


def test_swarm_prepares_messages_with_system_prompt() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent], system_prompt="Hello")

    messages = _swarm_prepare_messages(swarm, [])

    assert isinstance(messages[0], SystemMessage)
    assert _content_text(messages[0]) == "Hello"


def test_swarm_builds_invocation_tool_map() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    tool_map = _swarm_build_invocation_tool_map(swarm)
    assert agent.id in tool_map


def test_swarm_roster_entry_includes_nested_synthesis_metadata() -> None:
    agent = Agent(
        name="agent",
        description="Summarizes and reports findings",
        included_nested_synthesis="auto",
        memory_config=MemoryConfig(
            level="glimpse",
            skill_extraction=MemorySkillExtractionConfig(sharing_scope="agent"),
        ),
        skills=[
            {
                "skill_id": "skill-1",
                "name": "deploy_with_checks",
                "description": "Deploy service and run health checks before cutover.",
                "steps": [{"action": "deploy", "tool": "deploy_service"}],
            }
        ],
        resources=[
            {
                "resource_id": "doc-1",
                "title": "Deployment runbook",
                "description": "Production deployment runbook.",
            }
        ],
        client=_make_client(),
    )
    swarm = Swarm(name="swarm", agents=[agent])

    tool_map = _swarm_build_invocation_tool_map(swarm)
    roster_entry = _swarm_build_agent_roster_entry(swarm, agent, tool_map)

    assert roster_entry["included_nested_synthesis"] == "auto"
    guidance = roster_entry["included_nested_synthesis_guidance"]
    assert isinstance(guidance, str)
    assert guidance
    memory_cfg = cast(JsonObject, roster_entry["memory_config"])
    assert memory_cfg["level"] == "glimpse"
    skills = cast("list[JsonObject]", roster_entry["memory_defined_skills"])
    assert skills[0]["origin"] == "user_defined"
    assert skills[0]["sharing_scope"] == "agent"
    resources = cast("list[JsonObject]", roster_entry["memory_bound_resources"])
    assert resources[0]["resource_id"] == "doc-1"


def test_swarm_memory_config_override_entry_agent_defaults() -> None:
    agent = Agent(
        name="agent",
        memory_config=MemoryConfig(level="none"),
        client=_make_client(),
    )
    swarm = Swarm(
        name="swarm",
        agents=[agent],
        memory_config=MemoryConfig(
            level="glimpse",
            skill_extraction=MemorySkillExtractionConfig(sharing_scope="swarm"),
        ),
        skills=[
            {
                "skill_id": "skill-shared",
                "name": "shared_runbook",
                "description": "Use the shared runbook before deployment.",
                "steps": [{"action": "open runbook"}],
            }
        ],
    )

    state = SessionRequest(memory_config=MemoryConfig(level="none"))
    _swarm_enrich_state_metadata(swarm, state)

    assert isinstance(state.memory_config, MemoryConfig)
    assert state.memory_config.level == "glimpse"
    assert state.memory_assets_config is not None
    assert state.memory_assets_config.defined_skills[0].sharing_scope == "swarm"
    assert state.memory_assets_config.defined_skills[0].origin == "user_defined"


def test_swarm_enrich_state_metadata_encodes_inline_resources() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(
        name="swarm",
        agents=[agent],
        resources=[
            {
                "name": "swarm-runbook.txt",
                "mime_type": "text/plain",
                "text_content": "swarm runbook content",
                "binding_type": "swarm",
            }
        ],
    )

    state = SessionRequest(metadata={})
    _swarm_enrich_state_metadata(swarm, state)

    assert state.memory_assets_config is not None
    resources = state.memory_assets_config.bound_resources
    assert resources[0].name == "swarm-runbook.txt"
    assert resources[0].binding_type == "swarm"
    content_b64 = _bound_resource_content_base64(resources[0])
    assert base64.b64decode(content_b64 or "") == b"swarm runbook content"


def test_agent_compile_state_includes_memory_assets() -> None:
    agent = Agent(
        name="agent",
        memory_config=MemoryConfig(
            level="focus",
            skill_extraction=MemorySkillExtractionConfig(sharing_scope="agent"),
        ),
        skills=[
            {
                "skill_id": "compile-skill",
                "name": "compile_path_skill",
                "description": "Skill should be present in compiled metadata.",
            }
        ],
        resources=[
            {
                "resource_id": "compile-doc",
                "title": "Compile metadata runbook",
                "description": "Resource should be present in compiled metadata.",
            }
        ],
        client=_make_client(),
    )

    state = agent.compile_state([HumanMessage(content="hello")])

    assert isinstance(state.memory_config, MemoryConfig)
    assert state.memory_config.level == "focus"
    assert state.memory_assets_config is not None
    skills = state.memory_assets_config.defined_skills
    assert skills[0].skill_id == "compile-skill"
    resources = state.memory_assets_config.bound_resources
    assert resources[0].resource_id == "compile-doc"


def test_agent_invoke_rejects_reserved_memory_metadata_keys() -> None:
    agent = Agent(name="agent", client=_make_client())

    with pytest.raises(ValueError, match="use memory_config instead"):
        _ = agent.invoke(
            [HumanMessage(content="hello")],
            metadata={"memory_level": "glimpse"},
        )


def test_agent_stream_rejects_reserved_memory_metadata_keys() -> None:
    agent = Agent(name="agent", client=_make_client())

    with pytest.raises(ValueError, match="use memory_config instead"):
        _ = list(
            agent.stream(
                [HumanMessage(content="hello")],
                metadata={"memory_summarization_enabled": False},
            )
        )


def test_agent_invoke_rejects_reserved_session_control_metadata_keys() -> None:
    agent = Agent(name="agent", client=_make_client())

    with pytest.raises(ValueError, match="use typed session config fields instead"):
        _ = agent.invoke(
            [HumanMessage(content="hello")],
            metadata={"allowed_system_tools": ["web_search"]},
        )


def test_agent_rejects_project_scope_for_auto_insight_extraction() -> None:
    with pytest.raises(ValidationError, match="agent"):
        _ = Agent(
            name="agent",
            memory_config=MemoryConfig(
                # ``project`` is intentionally outside the declared ``Literal["agent", "swarm"]``
                # so the Agent-level validator rejects it; cast-through-object lets the test
                # exercise the rejection without the type-system flag (Pattern 2).
                insight_extraction=MemoryInsightExtractionConfig(
                    sharing_scope=cast('Literal["agent", "swarm"]', cast(object, "project")),
                ),
            ),
            client=_make_client(),
        )


def test_agent_del_ignores_partial_initialization() -> None:
    agent = Agent.__new__(Agent)

    agent.__del__()


def test_agent_del_skips_cleanup_during_interpreter_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(name="agent", client=_make_client())
    close_calls: list[str] = []

    def _close_stub(self: Agent) -> None:
        _ = self
        close_calls.append("close")

    monkeypatch.setattr(Agent, "close", _close_stub)
    monkeypatch.setattr(sys, "is_finalizing", lambda: True)

    agent.__del__()

    assert close_calls == []


def test_swarm_validate_force_final_tool_request() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    with pytest.raises(ValueError):
        _swarm_validate_force_final_tool_request(swarm, True)

    final_tool = FunctionTool(name="final", description="f", tool_id="final", func=lambda: "ok")
    final_tool.final_tool = True
    _swarm_add_tool(swarm, final_tool)

    _swarm_validate_force_final_tool_request(swarm, True)


def test_swarm_force_final_tool_rejects_swarm_final_tool_with_use_as_final_output() -> None:
    """Swarm-scope ``final_tool`` + sub-agent ``use_as_final_output`` is a
    conflict only when ``force_final_tool=True`` is invoked. The bare
    ``final_tool`` marker is just a positioning hint, so the combination
    is valid until forcing actually activates it."""
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    final_tool = FunctionTool(name="final", description="f", tool_id="final", func=lambda: "ok")
    final_tool.final_tool = True
    _swarm_add_tool(swarm, final_tool)
    agent.use_as_final_output = True

    # Without force_final_tool, the combination is allowed —
    # final_tool alone does not force the tool to be used.
    _swarm_validate_force_final_tool_request(swarm, False)

    # With force_final_tool=True, the conflict materializes.
    with pytest.raises(ValueError, match="conflicting final-output declarations"):
        _swarm_validate_force_final_tool_request(swarm, True)


def test_swarm_rejects_multiple_use_as_final_output_agents() -> None:
    """``validate_tool_configuration`` catches this at swarm invocation time."""
    agent_a = Agent(name="agent_a", client=_make_client())
    agent_b = Agent(name="agent_b", client=_make_client())
    agent_a.use_as_final_output = True
    agent_b.use_as_final_output = True

    swarm = Swarm(name="swarm", agents=[agent_a, agent_b])

    with pytest.raises(ValueError, match="Multiple swarm agents marked"):
        swarm.validate_on_invoke()


def test_swarm_force_final_tool_rejects_ambiguous_multi_agent_finals() -> None:
    agent_a = Agent(name="agent_a", client=_make_client())
    agent_b = Agent(name="agent_b", client=_make_client())

    tool_a = FunctionTool(name="final_a", description="f", tool_id="final_a", func=lambda: "ok")
    tool_a.final_tool = True
    _agent_add_tool(agent_a, tool_a)

    tool_b = FunctionTool(name="final_b", description="f", tool_id="final_b", func=lambda: "ok")
    tool_b.final_tool = True
    _agent_add_tool(agent_b, tool_b)

    swarm = Swarm(name="swarm", agents=[agent_a, agent_b])

    with pytest.raises(ValueError, match="ambiguous"):
        _swarm_validate_force_final_tool_request(swarm, True)

    agent_a.use_as_final_output = True
    _swarm_validate_force_final_tool_request(swarm, True)


def test_swarm_requires_agents_on_invoke() -> None:
    swarm = Swarm(name="swarm", agents=[])

    with pytest.raises(ValueError):
        swarm.validate_on_invoke()


def test_swarm_stream_returns_orchestrator_events() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    class _DummyOrchestrator:
        def compile_state(self, *args: object, **kwargs: object) -> SessionRequest:
            _ = (args, kwargs)
            return SessionRequest()

        def register_swarm_agent_tools(self, agent_tools: object) -> None:
            _ = agent_tools

        def stream_compiled_state(
            self,
            state: SessionRequest,
            *,
            thread_id: str | None = None,
            verbose: bool = False,
        ) -> Iterator[SSEEvent]:
            _ = (state, thread_id, verbose)
            yield SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "completed"})

    _override_swarm_build_orchestrator(swarm, lambda _agent: _DummyOrchestrator())

    events = list(swarm.stream([]))

    assert events and events[-1].name == FINAL_EVENT_NAME

    # Silence unused symbol that the dummy orchestrator stands in for at runtime.
    _ = Sequence
