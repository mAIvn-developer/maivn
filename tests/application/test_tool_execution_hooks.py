from __future__ import annotations

import base64
from typing import Any

from maivn_shared import FINAL_EVENT_NAME, HumanMessage, MemoryConfig, SessionResponse

from maivn._internal.api.agent import Agent
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.application_services.tool_execution import ToolExecutionService
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.entities.sse_event import SSEEvent


def test_tool_execution_hooks_order_and_error_handling() -> None:
    calls: list[tuple[str, str]] = []

    def swarm_hook(payload: dict[str, Any]) -> None:
        calls.append(("swarm", str(payload.get("stage"))))

    def agent_hook(payload: dict[str, Any]) -> None:
        calls.append(("agent", str(payload.get("stage"))))

    def tool_before(payload: dict[str, Any]) -> None:
        calls.append(("tool_before", str(payload.get("stage"))))

    def tool_after(payload: dict[str, Any]) -> None:
        stage = str(payload.get("stage"))
        error = payload.get("error")
        if error is None:
            calls.append(("tool_after_ok", stage))
        else:
            calls.append(("tool_after_error", stage))

    agent = Agent(api_key="test")
    agent.before_execute = agent_hook
    agent.after_execute = agent_hook

    swarm = Swarm(agents=[agent])
    swarm.before_execute = swarm_hook
    swarm.after_execute = swarm_hook

    @agent.toolify(description="ok tool", before_execute=tool_before, after_execute=tool_after)
    def ok_tool(x: int) -> int:
        return x + 1

    @agent.toolify(description="boom tool", before_execute=tool_before, after_execute=tool_after)
    def boom_tool() -> None:
        raise RuntimeError("boom")

    agent.compile_tools()
    swarm.compile_tools()

    svc = ToolExecutionService()
    svc.rebuild_index(agent.compile_tools())

    # Success path
    calls.clear()
    ctx = ExecutionContext(scope=agent)
    assert svc.execute_tool_call(ok_tool.tool_id, {"x": 1}, context=ctx) == 2

    assert calls == [
        ("swarm", "before"),
        ("agent", "before"),
        ("tool_before", "before"),
        ("tool_after_ok", "after"),
        ("agent", "after"),
        ("swarm", "after"),
    ]

    # Error path still runs after hooks
    calls.clear()
    try:
        svc.execute_tool_call(boom_tool.tool_id, {}, context=ctx)
    except RuntimeError:
        pass

    assert calls == [
        ("swarm", "before"),
        ("agent", "before"),
        ("tool_before", "before"),
        ("tool_after_error", "after"),
        ("agent", "after"),
        ("swarm", "after"),
    ]


def test_scope_hook_execution_mode_scope_suppresses_per_tool_scope_hooks() -> None:
    calls: list[tuple[str, str]] = []

    def swarm_hook(payload: dict[str, Any]) -> None:
        calls.append(("swarm", str(payload.get("stage"))))

    def agent_hook(payload: dict[str, Any]) -> None:
        calls.append(("agent", str(payload.get("stage"))))

    def tool_before(payload: dict[str, Any]) -> None:
        calls.append(("tool_before", str(payload.get("stage"))))

    def tool_after(payload: dict[str, Any]) -> None:
        calls.append(("tool_after", str(payload.get("stage"))))

    agent = Agent(api_key="test")
    agent.before_execute = agent_hook
    agent.after_execute = agent_hook
    agent.hook_execution_mode = "scope"

    swarm = Swarm(agents=[agent])
    swarm.before_execute = swarm_hook
    swarm.after_execute = swarm_hook
    swarm.hook_execution_mode = "scope"

    @agent.toolify(description="ok tool", before_execute=tool_before, after_execute=tool_after)
    def ok_tool(x: int) -> int:
        return x + 1

    agent.compile_tools()
    swarm.compile_tools()

    svc = ToolExecutionService()
    svc.rebuild_index(agent.compile_tools())

    calls.clear()
    ctx = ExecutionContext(scope=agent)
    assert svc.execute_tool_call(ok_tool.tool_id, {"x": 1}, context=ctx) == 2

    # In scope mode, swarm/agent hooks should NOT run per tool. Only tool hooks.
    assert calls == [
        ("tool_before", "before"),
        ("tool_after", "after"),
    ]


def test_swarm_hook_execution_mode_agent_runs_only_for_agent_tools() -> None:
    calls: list[tuple[str, str, str]] = []

    def swarm_hook(payload: dict[str, Any]) -> None:
        tool = payload.get("tool")
        tool_type = getattr(tool, "tool_type", None)
        calls.append(("swarm", str(payload.get("stage")), str(tool_type)))

    agent = Agent(api_key="test")

    swarm = Swarm(agents=[agent])
    swarm.before_execute = swarm_hook
    swarm.after_execute = swarm_hook
    swarm.hook_execution_mode = "agent"

    @agent.toolify(description="normal tool")
    def normal_tool(x: int) -> int:
        return x + 1

    @agent.toolify(description="agent tool")
    def fake_agent_tool(x: int) -> int:
        return x

    agent.compile_tools()
    swarm.compile_tools()

    # Force tool_type for this tool to emulate AgentTool execution.
    tool_obj = agent.get_tool(fake_agent_tool.tool_id)
    assert tool_obj is not None
    tool_obj.tool_type = "agent"

    svc = ToolExecutionService()
    svc.rebuild_index(agent.compile_tools())
    ctx = ExecutionContext(scope=agent)

    calls.clear()
    assert svc.execute_tool_call(normal_tool.tool_id, {"x": 1}, context=ctx) == 2
    assert calls == []

    calls.clear()
    assert svc.execute_tool_call(fake_agent_tool.tool_id, {"x": 1}, context=ctx) == 1
    assert calls == [
        ("swarm", "before", "agent"),
        ("swarm", "after", "agent"),
    ]


# MARK: Scope Metadata


def test_scope_hooks_receive_merged_metadata() -> None:
    seen: dict[str, Any] = {}

    def capture_hook(payload: dict[str, Any]) -> None:
        context = payload.get("context")
        seen["metadata"] = getattr(context, "metadata", None)
        seen["memory_config"] = getattr(context, "memory_config", None)
        seen["system_tools_config"] = getattr(context, "system_tools_config", None)
        seen["memory_assets_config"] = getattr(context, "memory_assets_config", None)

    class DummyOrchestrator:
        def invoke(self, *args: Any, **kwargs: Any) -> SessionResponse:
            return SessionResponse(response="ok")

    agent = Agent(api_key="test")
    agent.hook_execution_mode = "scope"
    agent.before_execute = capture_hook
    agent.memory_config = MemoryConfig(
        level="glimpse",
        skill_extraction={"sharing_scope": "agent"},
    )
    agent.skills = [
        {
            "skill_id": "skill-123",
            "name": "deploy_with_checks",
            "description": "Deploy service and validate health checks before cutover.",
            "steps": [{"action": "deploy", "tool": "deploy_service"}],
        }
    ]
    agent.resources = [
        {
            "resource_id": "doc-123",
            "title": "Deploy runbook",
            "description": "Runbook used by deploy agent.",
        }
    ]
    agent._orchestrator = DummyOrchestrator()

    agent.invoke(
        [HumanMessage(content="hello")],
        metadata={"existing": "value"},
        allow_private_in_system_tools=True,
    )

    metadata = seen.get("metadata")
    assert isinstance(metadata, dict)
    memory_config = seen.get("memory_config")
    assert isinstance(memory_config, MemoryConfig)
    assert memory_config.level == "glimpse"
    assert metadata.get("existing") == "value"
    system_tools_config = seen.get("system_tools_config")
    assert system_tools_config is not None
    assert system_tools_config.allow_private_data is True
    assert system_tools_config.allow_private_data_placeholders is True
    memory_assets_config = seen.get("memory_assets_config")
    assert memory_assets_config is not None
    skills = memory_assets_config.defined_skills
    assert skills[0].skill_id == "skill-123"
    assert skills[0].origin == "user_defined"
    assert skills[0].confidence == 1.0
    assert skills[0].sharing_scope == "agent"
    resources = memory_assets_config.bound_resources
    assert resources[0].resource_id == "doc-123"


def test_scope_hooks_encode_inline_resource_content() -> None:
    seen: dict[str, Any] = {}

    def capture_hook(payload: dict[str, Any]) -> None:
        context = payload.get("context")
        seen["memory_assets_config"] = getattr(context, "memory_assets_config", None)

    class DummyOrchestrator:
        def invoke(self, *args: Any, **kwargs: Any) -> SessionResponse:
            return SessionResponse(response="ok")

    agent = Agent(api_key="test")
    agent.hook_execution_mode = "scope"
    agent.before_execute = capture_hook
    agent.resources = [
        {
            "name": "deploy-notes.txt",
            "mime_type": "text/plain",
            "text_content": "deploy checklist",
            "binding_type": "agent",
        }
    ]
    agent._orchestrator = DummyOrchestrator()

    agent.invoke([HumanMessage(content="hello")])

    memory_assets_config = seen.get("memory_assets_config")
    assert memory_assets_config is not None
    resources = memory_assets_config.bound_resources
    assert resources[0].name == "deploy-notes.txt"
    assert resources[0].binding_type == "agent"
    assert base64.b64decode(resources[0].content_base64 or "") == b"deploy checklist"


def test_scope_hooks_do_not_inject_empty_bound_resources() -> None:
    seen: dict[str, Any] = {}

    def capture_hook(payload: dict[str, Any]) -> None:
        context = payload.get("context")
        seen["memory_assets_config"] = getattr(context, "memory_assets_config", None)

    class DummyOrchestrator:
        def invoke(self, *args: Any, **kwargs: Any) -> SessionResponse:
            return SessionResponse(response="ok")

    agent = Agent(api_key="test")
    agent.hook_execution_mode = "scope"
    agent.before_execute = capture_hook
    agent.skills = [
        {
            "skill_id": "skill-no-docs",
            "name": "memory_skill_only",
            "description": "Skill metadata without bound resources.",
        }
    ]
    agent._orchestrator = DummyOrchestrator()

    agent.invoke([HumanMessage(content="hello")])

    memory_assets_config = seen.get("memory_assets_config")
    assert memory_assets_config is not None
    assert memory_assets_config.bound_resources == []
    skills = memory_assets_config.defined_skills
    assert skills[0].skill_id == "skill-no-docs"


def test_scope_hooks_receive_stream_final_response() -> None:
    seen: dict[str, Any] = {}

    def capture_after_hook(payload: dict[str, Any]) -> None:
        seen["result"] = payload.get("result")

    class DummyOrchestrator:
        def stream(self, *args: Any, **kwargs: Any):  # noqa: ANN002, ANN003
            _ = (args, kwargs)
            yield SSEEvent(
                name=FINAL_EVENT_NAME,
                payload={"status": "completed", "responses": ["ok"]},
            )

    agent = Agent(api_key="test")
    agent.hook_execution_mode = "scope"
    agent.after_execute = capture_after_hook
    agent._orchestrator = DummyOrchestrator()  # type: ignore[assignment]

    events = list(agent.stream([HumanMessage(content="hello")]))

    assert events and events[-1].name == FINAL_EVENT_NAME
    assert isinstance(seen.get("result"), SessionResponse)
    assert seen["result"].responses == ["ok"]
