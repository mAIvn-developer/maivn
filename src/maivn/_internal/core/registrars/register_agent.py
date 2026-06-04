# pyright: strict
"""Agent registrar.
Registers agents with repositories and associates them with a swarm.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from ..interfaces.repositories.agent import AgentRepoInterface, AgentRepositoryEntity

# MARK: - Types

AgentT = TypeVar("AgentT", bound=AgentRepositoryEntity)


class AgentSwarmTarget(Protocol[AgentT]):
    @property
    def agents(self) -> list[AgentT]: ...


# MARK: - AgentRegistrar


class AgentRegistrar(Generic[AgentT]):
    """Register agents with repository management."""

    # MARK: - Initialization

    def __init__(self, repo: AgentRepoInterface[AgentT]) -> None:
        self._repo: AgentRepoInterface[AgentT] = repo

    # MARK: - Registration

    def __call__(self, swarm: AgentSwarmTarget[AgentT], agent: AgentT) -> None:
        """Register an agent with a swarm.

        Args:
            swarm: The swarm to register the agent to.
            agent: The agent to register.
        """
        self._register_to_repository(agent)
        self._ensure_agent_in_swarm_list(swarm, agent)

    # MARK: - Private Methods

    def _register_to_repository(self, agent: AgentT) -> None:
        """Add agent to the repository."""
        self._repo.add_agent(agent)

    def _ensure_agent_in_swarm_list(
        self,
        swarm: AgentSwarmTarget[AgentT],
        agent: AgentT,
    ) -> None:
        """Ensure agent is in swarm's agent list exactly once."""
        if agent not in swarm.agents:
            swarm.agents.append(agent)
