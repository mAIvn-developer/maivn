# pyright: strict
"""Agent repository interface.
Defines abstract methods for storing and retrieving Agent instances.
"""

from __future__ import annotations

from typing import Protocol, TypeVar


class AgentRepositoryEntity(Protocol):
    """Minimal shape an agent repository needs from an Agent-like object."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str | None: ...


AgentT = TypeVar("AgentT", bound=AgentRepositoryEntity)


class AgentRepoInterface(Protocol[AgentT]):
    """
    Interface for an agent repository.
    """

    # MARK: - Agent methods

    def add_agent(self, agent: AgentT) -> None:
        """
        Adds an agent to the agent repository.

        Args:
            agent: The agent to add.
        """
        ...

    def get_agent(self, agent_id: str) -> AgentT | None:
        """
        Gets an agent from the agent repository by ID.

        Args:
            agent_id: The ID of the agent to get.

        Returns:
            The agent with the given ID, or None if not found.
        """
        ...

    def get_agent_by_name(self, name: str) -> AgentT | None:
        """
        Gets an agent from the agent repository by name.

        Args:
            name: The name of the agent to get.

        Returns:
            The agent with the given name, or None if not found.
        """
        ...

    def list_agents(self) -> list[AgentT]:
        """
        Lists all agents in the agent repository.

        Returns:
            A list of all agents in the agent repository.
        """
        ...

    def remove_agent(self, agent_id: str) -> None:
        """
        Removes an agent from the agent repository.

        Args:
            agent_id: The ID of the agent to remove.
        """
        ...

    def update_agent(self, agent: AgentT) -> None:
        """
        Updates an agent in the agent repository.

        Args:
            agent: The agent to update.
        """
        ...
