from __future__ import annotations

import pytest
from maivn_shared import DataDependency

from maivn._internal.core.entities import FunctionTool
from maivn._internal.core.interfaces.repositories import DependencyRepoInterface, ToolRepoInterface
from maivn._internal.core.registrars.register_dependency import DependencyRegistrar
from maivn._internal.core.registrars.register_tools import ToolRegistrar


def _identity(value: int) -> int:
    return value


class _ToolRepo(ToolRepoInterface):
    def __init__(
        self,
        tools: list[FunctionTool] | None = None,
        *,
        raise_on_list: bool = False,
    ) -> None:
        self.store: dict[str, FunctionTool] = {}
        self._tools = list(tools or [])
        self.raise_on_list = raise_on_list
        self.list_calls = 0
        self.added: list[FunctionTool] = []

    def add_tool(self, tool: FunctionTool) -> None:
        self.added.append(tool)
        self.store[tool.tool_id] = tool
        self._tools.append(tool)

    def get_tool(self, tool_id: str) -> FunctionTool | None:
        return self.store.get(tool_id)

    def get_tool_by_name(self, name: str) -> FunctionTool | None:
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None

    def list_tools(self) -> list[FunctionTool]:
        self.list_calls += 1
        if self.raise_on_list:
            raise RuntimeError("boom")
        return list(self._tools)

    def remove_tool(self, tool_id: str) -> None:
        self.store.pop(tool_id, None)

    def update_tool(self, tool: FunctionTool) -> None:
        self.store[tool.tool_id] = tool


class _DependencyRepo(DependencyRepoInterface):
    def __init__(self) -> None:
        self.store: dict[str, list[DataDependency]] = {}
        self.calls: list[tuple[str, DataDependency]] = []

    def add_dependency(self, tool_id: str, dependency: DataDependency) -> None:
        self.calls.append((tool_id, dependency))
        self.store.setdefault(tool_id, []).append(dependency)

    def get_dependency(self, dependency_id: str) -> DataDependency | None:
        for dependencies in self.store.values():
            for dependency in dependencies:
                if dependency.arg_name == dependency_id:
                    return dependency
        return None

    def list_dependencies(self, tool_id: str) -> list[DataDependency]:
        return list(self.store.get(tool_id, []))

    def remove_dependency(self, dependency_id: str) -> None:
        for tool_id, dependencies in self.store.items():
            self.store[tool_id] = [dep for dep in dependencies if dep.arg_name != dependency_id]

    def update_dependency(self, dependency_id: str, dependency: DataDependency) -> None:
        self.remove_dependency(dependency_id)
        self.add_dependency("updated", dependency)


def _tool(name: str, *, tool_id: str | None = None, final_tool: bool = False) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=f"{name} tool",
        tool_id=tool_id or name,
        func=_identity,
        final_tool=final_tool,
    )


def test_tool_registrar_adds_non_final_tool_without_listing_existing_tools() -> None:
    repo = _ToolRepo()
    registrar = ToolRegistrar(repo)
    tool = _tool("alpha")

    registrar(tool)

    assert repo.added == [tool]
    assert repo.list_calls == 0


def test_tool_registrar_allows_first_final_tool() -> None:
    repo = _ToolRepo()
    registrar = ToolRegistrar(repo)
    tool = _tool("final", final_tool=True)

    registrar(tool)

    assert repo.added == [tool]
    assert repo.list_calls == 1


def test_tool_registrar_rejects_duplicate_final_tool_with_existing_names() -> None:
    repo = _ToolRepo([_tool("existing-final", final_tool=True)])
    registrar = ToolRegistrar(repo)

    with pytest.raises(ValueError, match="existing-final"):
        registrar(_tool("second-final", final_tool=True))

    assert repo.added == []


def test_tool_registrar_wraps_repository_errors_when_listing_final_tools() -> None:
    repo = _ToolRepo(raise_on_list=True)
    registrar = ToolRegistrar(repo)

    with pytest.raises(RuntimeError, match="Failed to list tools"):
        registrar(_tool("final", final_tool=True))


def test_dependency_registrar_forwards_dependency_to_repository() -> None:
    repo = _DependencyRepo()
    registrar = DependencyRegistrar(repo)
    dependency = DataDependency(arg_name="payload", data_key="payload")

    registrar("tool-1", dependency)

    assert repo.calls == [("tool-1", dependency)]
    assert repo.list_dependencies("tool-1") == [dependency]
