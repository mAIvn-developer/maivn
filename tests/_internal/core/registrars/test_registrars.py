# pyright: strict
from __future__ import annotations

import pytest
from typing_extensions import override

from maivn._internal.core.entities import FunctionTool
from maivn._internal.core.entities.tools.base_tool import BaseTool
from maivn._internal.core.interfaces.repositories import ToolRepoInterface
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
        self.store: dict[str, BaseTool] = {}
        self._tools: list[FunctionTool] = list(tools or [])
        self.raise_on_list: bool = raise_on_list
        self.list_calls: int = 0
        self.added: list[FunctionTool] = []

    @override
    def add_tool(self, tool: BaseTool) -> None:
        assert isinstance(tool, FunctionTool)
        self.added.append(tool)
        self.store[tool.tool_id] = tool
        self._tools.append(tool)

    @override
    def get_tool(self, tool_id: str) -> BaseTool | None:
        return self.store.get(tool_id)

    @override
    def get_tool_by_name(self, name: str) -> BaseTool | None:
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None

    @override
    def list_tools(self) -> list[BaseTool]:
        self.list_calls += 1
        if self.raise_on_list:
            raise RuntimeError("boom")
        return list(self._tools)

    @override
    def remove_tool(self, tool_id: str) -> None:
        _ = self.store.pop(tool_id, None)

    @override
    def update_tool(self, tool: BaseTool) -> None:
        self.store[tool.tool_id] = tool


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
