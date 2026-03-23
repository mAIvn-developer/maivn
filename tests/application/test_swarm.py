from __future__ import annotations

import base64

import pytest
from maivn_shared import FINAL_EVENT_NAME, HumanMessage, MemoryConfig, SessionRequest, SystemMessage
from pydantic import ValidationError

import maivn._internal.api.agent.agent as agent_module
from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.entities import FunctionTool, SSEEvent
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def test_swarm_prepares_messages_with_system_prompt() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent], system_prompt="Hello")

    messages = swarm._prepare_messages([])

    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "Hello"


def test_swarm_builds_invocation_tool_map() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    tool_map = swarm._build_invocation_tool_map()
    assert agent.id in tool_map


def test_swarm_roster_entry_includes_nested_synthesis_metadata() -> None:
    agent = Agent(
        name="agent",
        description="Summarizes and reports findings",
        included_nested_synthesis="Auto",
        memory_config={"level": "glimpse", "skill_extraction": {"sharing_scope": "agent"}},
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

    tool_map = swarm._build_invocation_tool_map()
    roster_entry = swarm._build_agent_roster_entry(agent, tool_map)

    assert roster_entry["included_nested_synthesis"] == "auto"
    assert isinstance(roster_entry["included_nested_synthesis_guidance"], str)
    assert roster_entry["included_nested_synthesis_guidance"]
    assert roster_entry["memory_config"]["level"] == "glimpse"
    assert roster_entry["memory_defined_skills"][0]["origin"] == "user_defined"
    assert roster_entry["memory_defined_skills"][0]["sharing_scope"] == "agent"
    assert roster_entry["memory_bound_resources"][0]["resource_id"] == "doc-1"


def test_swarm_memory_config_override_entry_agent_defaults() -> None:
    agent = Agent(
        name="agent",
        memory_config={"level": "none"},
        client=_make_client(),
    )
    swarm = Swarm(
        name="swarm",
        agents=[agent],
        memory_config={"level": "glimpse", "skill_extraction": {"sharing_scope": "swarm"}},
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
    swarm._enrich_state_metadata(state)

    assert state.metadata is not None
    assert isinstance(state.memory_config, MemoryConfig)
    assert state.memory_config.level == "glimpse"
    assert state.metadata["memory_defined_skills"][0]["sharing_scope"] == "swarm"
    assert state.metadata["memory_defined_skills"][0]["origin"] == "user_defined"


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
    swarm._enrich_state_metadata(state)

    assert state.metadata is not None
    resources = state.metadata.get("memory_bound_resources")
    assert isinstance(resources, list)
    assert resources[0]["name"] == "swarm-runbook.txt"
    assert resources[0]["binding_type"] == "swarm"
    assert base64.b64decode(resources[0]["content_base64"]) == b"swarm runbook content"


def test_agent_compile_state_includes_memory_assets() -> None:
    agent = Agent(
        name="agent",
        memory_config={"level": "focus", "skill_extraction": {"sharing_scope": "agent"}},
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

    assert state.metadata is not None
    assert isinstance(state.memory_config, MemoryConfig)
    assert state.memory_config.level == "focus"
    skills = state.metadata.get("memory_defined_skills")
    assert isinstance(skills, list)
    assert skills[0]["skill_id"] == "compile-skill"
    resources = state.metadata.get("memory_bound_resources")
    assert isinstance(resources, list)
    assert resources[0]["resource_id"] == "compile-doc"


def test_agent_invoke_rejects_reserved_memory_metadata_keys() -> None:
    agent = Agent(name="agent", client=_make_client())

    with pytest.raises(ValueError, match="use memory_config instead"):
        agent.invoke(
            [HumanMessage(content="hello")],
            metadata={"memory_level": "glimpse"},
        )


def test_agent_stream_rejects_reserved_memory_metadata_keys() -> None:
    agent = Agent(name="agent", client=_make_client())

    with pytest.raises(ValueError, match="use memory_config instead"):
        list(
            agent.stream(
                [HumanMessage(content="hello")],
                metadata={"memory_summarization_enabled": False},
            )
        )


def test_agent_rejects_project_scope_for_auto_insight_extraction() -> None:
    with pytest.raises(ValidationError, match="agent"):
        Agent(
            name="agent",
            memory_config={"insight_extraction": {"sharing_scope": "project"}},
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
    monkeypatch.setattr(agent_module.sys, "is_finalizing", lambda: True)

    agent.__del__()

    assert close_calls == []


def test_swarm_validate_force_final_tool_request() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    with pytest.raises(ValueError):
        swarm._validate_force_final_tool_request(True)

    final_tool = FunctionTool(name="final", description="f", tool_id="final", func=lambda: "ok")
    final_tool.final_tool = True
    swarm._tool_repo.add_tool(final_tool)
    agent.use_as_final_output = True

    with pytest.raises(ValueError):
        swarm._validate_force_final_tool_request(True)


def test_swarm_requires_agents_on_invoke() -> None:
    swarm = Swarm(name="swarm", agents=[])

    with pytest.raises(ValueError):
        swarm.validate_on_invoke()


def test_swarm_stream_returns_orchestrator_events() -> None:
    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    class _DummyOrchestrator:
        def compile_state(self, *args, **kwargs):  # noqa: ANN002, ANN003
            _ = (args, kwargs)
            return SessionRequest()

        def _register_swarm_agent_tools(self, agent_tools):  # noqa: ANN001
            _ = agent_tools
            return None

        def stream_compiled_state(self, state, *, thread_id=None, verbose=False):  # noqa: ANN001
            _ = (state, thread_id, verbose)
            yield SSEEvent(name=FINAL_EVENT_NAME, payload={"status": "completed"})

    swarm._build_orchestrator = lambda _agent: _DummyOrchestrator()  # type: ignore[method-assign]

    events = list(swarm.stream([]))

    assert events and events[-1].name == FINAL_EVENT_NAME
