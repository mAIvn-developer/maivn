"""In-memory tool repository implementation.
Stores tools by id and maintains a secondary name index for lookup.
"""

from __future__ import annotations

# MARK: In-Memory Tool Repository
from typing import TYPE_CHECKING

from maivn._internal.core.interfaces.repositories import ToolRepoInterface

if TYPE_CHECKING:
    from maivn._internal.core.entities.tools import BaseTool


class ToolRepo(ToolRepoInterface):
    """In-memory implementation of ToolRepoInterface.

    Performance optimization: Maintains secondary index for O(1) name lookups.
    """

    def __init__(self) -> None:
        self.store: dict[str, BaseTool] = {}
        self._name_index: dict[str, BaseTool] = {}

    # MARK: - Tool methods

    def _get_tool_id(self, tool: BaseTool) -> str | None:
        return getattr(tool, "tool_id", None) or getattr(tool, "id", None)

    def _get_tool_name(self, tool: BaseTool) -> str | None:
        return getattr(tool, "name", None)

    def _add_to_name_index(self, tool: BaseTool) -> None:
        name = self._get_tool_name(tool)
        if name:
            self._name_index[name] = tool

    def _remove_from_name_index(self, tool: BaseTool) -> None:
        name = self._get_tool_name(tool)
        if name and name in self._name_index:
            del self._name_index[name]

    def add_tool(self, tool: BaseTool) -> None:
        if tool is None:
            return
        tool_id = self._get_tool_id(tool)
        if not tool_id or tool_id in self.store:
            return
        self.store[tool_id] = tool
        self._add_to_name_index(tool)

    def get_tool(self, tool_id: str) -> BaseTool | None:
        return self.store.get(tool_id)

    def get_tool_by_name(self, name: str) -> BaseTool | None:
        return self._name_index.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self.store.values())

    def remove_tool(self, tool_id: str) -> None:
        tool = self.store.pop(tool_id, None)
        if tool:
            self._remove_from_name_index(tool)

    def update_tool(self, tool: BaseTool) -> None:
        tool_id = self._get_tool_id(tool)
        if not tool_id:
            return
        if tool_id in self.store:
            self._remove_from_name_index(self.store[tool_id])
        self.store[tool_id] = tool
        self._add_to_name_index(tool)
