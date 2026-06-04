# pyright: strict
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from maivn_shared import DataDependency, InterruptDependency, ToolDependency

from maivn._internal.adapters.repositories import AgentRepo, DependencyRepo, ToolRepo

if TYPE_CHECKING:
    from maivn._internal.core.entities.tools.base_tool import BaseTool


@dataclass
class _Agent:
    id: str
    name: str


@dataclass
class _Tool:
    tool_id: str
    name: str


def _compute_key(repo: DependencyRepo, dependency: object) -> str:
    """Read the protected compute helper without leaking the underscore name."""
    from collections.abc import Callable

    helper = cast("Callable[..., str]", repo.compute_dependency_key)
    return helper(dependency)


def test_agent_repo_add_update_remove() -> None:
    repo: AgentRepo[_Agent] = AgentRepo()
    agent = _Agent(id="a1", name="alpha")

    repo.add_agent(agent)
    assert repo.get_agent("a1") is agent
    assert repo.get_agent_by_name("alpha") is agent

    updated = _Agent(id="a1", name="beta")
    repo.update_agent(updated)
    assert repo.get_agent_by_name("beta") is updated
    assert repo.get_agent_by_name("alpha") is None

    repo.remove_agent("a1")
    assert repo.get_agent("a1") is None


def test_tool_repo_add_update_remove() -> None:
    repo = ToolRepo()
    tool = cast("BaseTool", cast(object, _Tool(tool_id="t1", name="tool")))

    repo.add_tool(tool)
    assert repo.get_tool("t1") is tool
    assert repo.get_tool_by_name("tool") is tool

    updated = cast("BaseTool", cast(object, _Tool(tool_id="t1", name="tool-v2")))
    repo.update_tool(updated)
    assert repo.get_tool_by_name("tool-v2") is updated
    assert repo.get_tool_by_name("tool") is None

    repo.remove_tool("t1")
    assert repo.get_tool("t1") is None


def test_dependency_repo_add_get_update_remove() -> None:
    repo = DependencyRepo()
    dependency = DataDependency(arg_name="data", data_key="data")
    repo.add_dependency("tool-1", dependency)

    dep_id = _compute_key(repo, dependency)
    assert dep_id is not None
    assert repo.get_dependency(dep_id) is dependency
    assert repo.list_dependencies("tool-1") == [dependency]

    updated = ToolDependency(arg_name="tool", tool_id="tool-2")
    repo.update_dependency(dep_id, updated)
    updated_id = _compute_key(repo, updated)
    assert updated_id is not None
    assert repo.get_dependency(updated_id) is updated

    repo.remove_dependency(dep_id)
    assert repo.get_dependency(dep_id) is None


def test_dependency_repo_interrupt_dependency_id() -> None:
    repo = DependencyRepo()
    dependency = InterruptDependency(
        arg_name="answer", prompt="Enter", input_handler=lambda x: "ok"
    )

    dep_id = _compute_key(repo, dependency)
    assert dep_id is not None

    repo.add_dependency("tool-2", dependency)
    assert repo.get_dependency(dep_id) is dependency
