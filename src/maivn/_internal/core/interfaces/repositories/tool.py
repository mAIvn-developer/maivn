"""Tool repository interface.
Defines abstract methods for storing and retrieving tool instances.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from maivn._internal.core.entities.tools import BaseTool


class ToolRepoInterface(ABC):
    """
    Interface for a tool repository.
    """

    store: dict[str, BaseTool]

    @abstractmethod
    def add_tool(self, tool: BaseTool) -> None:
        """
        Adds a tool to the tool repository.

        Args:
            tool: The tool to add.
        """
        raise NotImplementedError

    @abstractmethod
    def get_tool(self, tool_id: str) -> BaseTool | None:
        """
        Gets a tool from the tool repository by ID.

        Args:
            tool_id: The ID of the tool to get.
        Returns:
            The tool with the specified ID, or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def get_tool_by_name(self, name: str) -> BaseTool | None:
        """
        Get a tool by its name.

        Args:
            name: The name of the tool to get.

        Returns:
            The tool with the specified name, or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def list_tools(self) -> list[BaseTool]:
        """
        Gets all tools from the tool repository.

        Returns:
            A list of all tools in the tool repository.
        """
        raise NotImplementedError

    @abstractmethod
    def remove_tool(self, tool_id: str) -> None:
        """
        Removes a tool from the tool repository.

        Args:
            tool_id: The ID of the tool to remove.
        """
        raise NotImplementedError

    @abstractmethod
    def update_tool(self, tool: BaseTool) -> None:
        """
        Updates a tool in the tool repository.

        Args:
            tool: The tool to update.
        """
        raise NotImplementedError
