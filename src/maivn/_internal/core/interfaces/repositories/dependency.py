"""Dependency repository interface.
Defines abstract methods for storing and retrieving tool dependencies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from maivn_shared import BaseDependency

# MARK: Dependency Repo Interface


class DependencyRepoInterface(ABC):
    """
    Interface for a dependency repository.
    """

    store: dict[str, list[BaseDependency]]

    @abstractmethod
    def add_dependency(self, tool_id: str, dependency: BaseDependency) -> None:
        """
        Adds a dependency to the dependency repository.

        Args:
            tool_id: The ID of the tool to add the dependency to.
            dependency: The dependency to add.
        """
        raise NotImplementedError

    @abstractmethod
    def get_dependency(self, dependency_id: str) -> BaseDependency | None:
        """
        Gets a dependency from the dependency repository.

        Args:
            dependency_id: The ID of the dependency to get.

        Returns:
            The dependency with the given ID.
        """
        raise NotImplementedError

    @abstractmethod
    def list_dependencies(self, tool_id: str) -> list[BaseDependency]:
        """
        Gets all dependencies from the dependency repository for a specific tool.

        Args:
            tool_id: The ID of the tool to get the dependencies from.

        Returns:
            A list of all dependencies for the given tool.
        """
        raise NotImplementedError

    @abstractmethod
    def remove_dependency(self, dependency_id: str) -> None:
        """
        Removes a dependency from the dependency repository.

        Args:
            dependency_id: The ID of the dependency to remove.
        """
        raise NotImplementedError

    @abstractmethod
    def update_dependency(self, dependency_id: str, dependency: BaseDependency) -> None:
        """
        Updates a dependency in the dependency repository.

        Args:
            dependency_id: The ID of the dependency to update.
            dependency: The dependency to update.
        """
        raise NotImplementedError
