from __future__ import annotations

from dataclasses import dataclass

from maivn._internal.core.orchestrator.tooling import ToolIndexCoordinator


@dataclass
class _Tool:
    tool_id: str
    name: str
    tool_type: str = "func"
    target_agent_id: str | None = None


class _ToolExecution:
    def __init__(self) -> None:
        self._tool_index: dict[str, _Tool] = {}
        self.registered: list[_Tool] = []
        self.rebuilt: list[_Tool] = []

    def rebuild_index(self, tools: list[_Tool]) -> None:
        self.rebuilt = tools

    def _register_tool(self, tool: _Tool) -> None:
        self._tool_index[tool.tool_id] = tool
        self._tool_index[tool.name] = tool
        self.registered.append(tool)


class _ToolExecOrchestrator:
    def __init__(self) -> None:
        self.cleared = False

    def clear_results(self) -> None:
        self.cleared = True


class _StateCompiler:
    def __init__(self, dynamic_tools: list[_Tool] | None = None) -> None:
        self._dynamic_tools = dynamic_tools or []


class _Agent:
    def __init__(self, name: str, tools: list[_Tool]) -> None:
        self.name = name
        self._tools = tools

    def list_tools(self) -> list[_Tool]:
        return list(self._tools)


def test_tool_agent_lookup_falls_back_to_agent() -> None:
    coordinator = ToolIndexCoordinator(
        tool_execution=_ToolExecution(),
        tool_exec_orchestrator=_ToolExecOrchestrator(),
        state_compiler=_StateCompiler(),
        agent=_Agent("agent", []),
    )

    assert coordinator.tool_agent_lookup("missing") == "agent"


def test_build_tool_agent_mapping_with_swarm_and_dynamic_tools() -> None:
    tool_exec = _ToolExecution()
    coordinator = ToolIndexCoordinator(
        tool_execution=tool_exec,
        tool_exec_orchestrator=_ToolExecOrchestrator(),
        state_compiler=_StateCompiler(dynamic_tools=[_Tool("dyn", "dyn")]),
        agent=_Agent("agent", []),
    )

    agent_a = _Agent("alpha", [_Tool("t1", "tool-1")])
    agent_b = _Agent("beta", [_Tool("t2", "tool-2")])
    swarm = type("Swarm", (), {"agents": [agent_a, agent_b]})()

    coordinator.build_tool_agent_mapping(swarm)

    assert coordinator.tool_agent_lookup("t1") == "alpha"
    assert coordinator.tool_agent_lookup("t2") == "beta"
    assert coordinator.tool_agent_lookup("dyn") == "agent"
    assert coordinator.agent_count == 2


def test_register_swarm_agent_tools_handles_duplicates() -> None:
    tool_exec = _ToolExecution()
    tool_exec._tool_index["agent-tool"] = _Tool(
        "agent-tool", "agent", tool_type="agent", target_agent_id="agent-1"
    )

    coordinator = ToolIndexCoordinator(
        tool_execution=tool_exec,
        tool_exec_orchestrator=_ToolExecOrchestrator(),
        state_compiler=_StateCompiler(),
        agent=_Agent("agent", []),
    )

    dup_tool = _Tool("agent-tool", "agent", tool_type="agent", target_agent_id="agent-1")
    new_tool = _Tool("agent-tool-2", "agent2", tool_type="agent", target_agent_id="agent-2")

    coordinator.register_swarm_agent_tools([dup_tool, new_tool])

    assert new_tool in tool_exec.registered
    assert coordinator.tool_agent_lookup("agent-tool") == "agent"
    assert coordinator.tool_agent_lookup("agent2") == "agent2"
