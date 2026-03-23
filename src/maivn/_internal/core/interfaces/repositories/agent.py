"""Agent repository interface.
Defines abstract methods for storing and retrieving Agent instances.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maivn._internal.api.agent import Agent


class AgentRepoInterface(ABC):
    """
    Interface for an agent repository.
    """

    store: dict[str, Agent]

    # MARK: - Agent methods

    @abstractmethod
    def add_agent(self, agent: Agent) -> None:
        """
        Adds an agent to the agent repository.

        Args:
            agent: The agent to add.
        """
        raise NotImplementedError

    @abstractmethod
    def get_agent(self, agent_id: str) -> Agent | None:
        """
        Gets an agent from the agent repository by ID.

        Args:
            agent_id: The ID of the agent to get.

        Returns:
            The agent with the given ID, or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def get_agent_by_name(self, name: str) -> Agent | None:
        """
        Gets an agent from the agent repository by name.

        Args:
            name: The name of the agent to get.

        Returns:
            The agent with the given name, or None if not found.
        """
        raise NotImplementedError

    @abstractmethod
    def list_agents(self) -> list[Agent]:
        """
        Lists all agents in the agent repository.

        Returns:
            A list of all agents in the agent repository.
        """
        raise NotImplementedError

    @abstractmethod
    def remove_agent(self, agent_id: str) -> None:
        """
        Removes an agent from the agent repository.

        Args:
            agent_id: The ID of the agent to remove.
        """
        raise NotImplementedError

    @abstractmethod
    def update_agent(self, agent: Agent) -> None:
        """
        Updates an agent in the agent repository.

        Args:
            agent: The agent to update.
        """
        raise NotImplementedError
