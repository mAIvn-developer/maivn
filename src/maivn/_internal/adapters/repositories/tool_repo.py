"""In-memory tool repository implementation.
Stores tools by id and maintains a secondary name index for lookup.
"""

# pyright: strict
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from typing_extensions import override

from ...core.interfaces.repositories import ToolRepoInterface
from ._base import NameIndexedRepo

if TYPE_CHECKING:
    from ...core.entities.tools import BaseTool


# MARK: In-Memory Tool Repository


class ToolRepo(NameIndexedRepo[object], ToolRepoInterface):
    """In-memory implementation of ToolRepoInterface.

    Performance optimization: Maintains secondary index for O(1) name lookups.
    """

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[str, BaseTool] = {}

    # MARK: - Entity Hooks

    @override
    def _get_entity_id(self, entity: object) -> str | None:
        identifier = getattr(entity, "tool_id", None) or getattr(entity, "id", None)
        return cast(str | None, identifier)

    # MARK: - Tool methods

    @override
    def add_tool(self, tool: BaseTool) -> None:
        self._add(tool)

    @override
    def get_tool(self, tool_id: str) -> BaseTool | None:
        return cast("BaseTool | None", self._get(tool_id))

    @override
    def get_tool_by_name(self, name: str) -> BaseTool | None:
        return cast("BaseTool | None", self._get_by_name(name))

    @override
    def list_tools(self) -> list[BaseTool]:
        return cast("list[BaseTool]", self._list())

    @override
    def remove_tool(self, tool_id: str) -> None:
        self._remove(tool_id)

    @override
    def update_tool(self, tool: BaseTool) -> None:
        self._update(tool)
