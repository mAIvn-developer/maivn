# pyright: strict
from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from maivn._internal.api.agent import Agent
from maivn._internal.core.entities import (
    AgentTool,
    FunctionTool,
    McpTool,
    MethodTool,
    ModelTool,
)
from maivn._internal.core.orchestrator.tooling import ToolIndexCoordinator

# Mirror the SUT type alias so the test stubs satisfy the resolve_tool Protocol exactly.
RuntimeTool = FunctionTool | MethodTool | ModelTool | McpTool


@dataclass
class _Tool:
    tool_id: str
    name: str
    tool_type: str = "func"
    target_agent_id: str | None = None


class _ToolExecution:
    """Stub satisfying `_ToolExecutionIndex` Protocol.

    S10d NEW-OUT-3: `_ToolExecutionIndex` requires `resolve_tool` in addition to
    `rebuild_index`. The stub raises AttributeError on the test-only resolution path
    so the SUT's `except AttributeError` fallback (lookup via `_tool_index`) runs.
    """

    def __init__(self) -> None:
        self._tool_index: dict[str, _Tool] = {}
        self.registered: list[_Tool] = []
        self.rebuilt: list[_Tool] = []

    def rebuild_index(self, tools: list[RuntimeTool]) -> None:
        # Cast back to the test's stub type for storage; the test never asserts on
        # this beyond identity, so the runtime is intentionally loose.
        self.rebuilt = cast(list[_Tool], cast(object, tools))

    def resolve_tool(self, tool_id: str) -> RuntimeTool:
        # Forces the SUT into its AttributeError fallback so it consults `_tool_index`.
        raise AttributeError(tool_id)

    def _register_tool(self, tool: _Tool) -> None:
        self._tool_index[tool.tool_id] = tool
        self._tool_index[tool.name] = tool
        self.registered.append(tool)


class _ToolExecOrchestrator:
    def __init__(self) -> None:
        self.cleared: bool = False

    def clear_results(self) -> None:
        self.cleared = True


@dataclass
class _StateCompiler:
    _dynamic_tools: list[FunctionTool] = field(default_factory=list)


class _Agent:
    def __init__(self, name: str, tools: list[_Tool]) -> None:
        self.name: str = name
        self._tools: list[_Tool] = tools

    def list_tools(self) -> list[_Tool]:
        return list(self._tools)


def _make_agent_stub(name: str, tools: list[_Tool]) -> Agent:
    """Pattern 2 boundary cast for the Agent protocol position.

    `_Agent` only implements `name` + `list_tools`, the slice ToolIndexCoordinator
    actually exercises in these tests.
    """
    return cast(Agent, cast(object, _Agent(name, tools)))


def test_tool_agent_lookup_falls_back_to_agent() -> None:
    coordinator = ToolIndexCoordinator(
        tool_execution=_ToolExecution(),
        tool_exec_orchestrator=_ToolExecOrchestrator(),
        state_compiler=_StateCompiler(),
        agent=_make_agent_stub("agent", []),
    )

    assert coordinator.tool_agent_lookup("missing") == "agent"


def test_build_tool_agent_mapping_with_swarm_and_dynamic_tools() -> None:
    tool_exec = _ToolExecution()
    # The dynamic-tools slot is typed `list[FunctionTool]`; the test fills it with
    # a stub _Tool whose Protocol surface (`tool_id` + `name`) is sufficient for
    # `_register_tool_mapping`. Pattern 2 boundary cast applies.
    dynamic_tools = cast(list[FunctionTool], cast(object, [_Tool("dyn", "dyn")]))
    coordinator = ToolIndexCoordinator(
        tool_execution=tool_exec,
        tool_exec_orchestrator=_ToolExecOrchestrator(),
        state_compiler=_StateCompiler(_dynamic_tools=dynamic_tools),
        agent=_make_agent_stub("agent", []),
    )

    agent_a = _Agent("alpha", [_Tool("t1", "tool-1")])
    agent_b = _Agent("beta", [_Tool("t2", "tool-2")])
    # Swarm placeholder: ToolIndexCoordinator only reads `.agents` and uses each
    # agent's `name` + `list_tools()` surfaces — the swarm shim mirrors those.

    class _SwarmStub:
        def __init__(self, agents: list[_Agent]) -> None:
            self.agents: list[_Agent] = agents

    from maivn._internal.api.swarm import Swarm

    swarm = cast(Swarm, cast(object, _SwarmStub([agent_a, agent_b])))

    coordinator.build_tool_agent_mapping(swarm)

    assert coordinator.tool_agent_lookup("t1") == "alpha"
    assert coordinator.tool_agent_lookup("t2") == "beta"
    assert coordinator.tool_agent_lookup("dyn") == "agent"
    assert coordinator.agent_count == 2


def test_register_swarm_agent_tools_handles_duplicates() -> None:
    tool_exec = _ToolExecution()
    # `_tool_index` is module-private to the stub; the test owns the stub so
    # priming it directly is intentional. ``cast(..., getattr(...))`` keeps
    # the access out of ``reportPrivateUsage`` AND ``reportAny``.
    tool_index = cast(dict[str, _Tool], getattr(tool_exec, "_tool_index"))  # noqa: B009
    tool_index["agent-tool"] = _Tool(
        "agent-tool", "agent", tool_type="agent", target_agent_id="agent-1"
    )

    coordinator = ToolIndexCoordinator(
        tool_execution=tool_exec,
        tool_exec_orchestrator=_ToolExecOrchestrator(),
        state_compiler=_StateCompiler(),
        agent=_make_agent_stub("agent", []),
    )

    dup_tool = _Tool("agent-tool", "agent", tool_type="agent", target_agent_id="agent-1")
    new_tool = _Tool("agent-tool-2", "agent2", tool_type="agent", target_agent_id="agent-2")

    # AgentTool inherits FunctionTool, but the coordinator only relies on a
    # narrow surface (`tool_id`, `name`, `tool_type`, `target_agent_id`) so the
    # Pattern 2 boundary cast keeps the stubs lightweight.
    agent_tools = cast(list[AgentTool], cast(object, [dup_tool, new_tool]))

    coordinator.register_swarm_agent_tools(agent_tools)

    assert new_tool in tool_exec.registered
    assert coordinator.tool_agent_lookup("agent-tool") == "agent"
    assert coordinator.tool_agent_lookup("agent2") == "agent2"
