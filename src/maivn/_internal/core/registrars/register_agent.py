"""Agent registrar.
Registers agents with repositories and associates them with a swarm.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.core.interfaces.repositories import AgentRepoInterface

if TYPE_CHECKING:
    from maivn._internal.api.agent import Agent
    from maivn._internal.api.swarm import Swarm


# MARK: - AgentRegistrar


class AgentRegistrar:
    """Register agents with repository management."""

    # MARK: - Initialization

    def __init__(self, repo: AgentRepoInterface) -> None:
        self._repo = repo

    # MARK: - Registration

    def __call__(self, swarm: Swarm, agent: Agent) -> None:
        """Register an agent with a swarm.

        Args:
            swarm: The swarm to register the agent to.
            agent: The agent to register.
        """
        self._register_to_repository(agent)
        self._associate_with_swarm(swarm, agent)

    # MARK: - Private Methods

    def _register_to_repository(self, agent: Agent) -> None:
        """Add agent to the repository."""
        self._repo.add_agent(agent)

    def _associate_with_swarm(self, swarm: Swarm, agent: Agent) -> None:
        """Associate agent with swarm and add to swarm's agent list."""
        agent._swarm = swarm
        self._ensure_agent_in_swarm_list(swarm, agent)

    def _ensure_agent_in_swarm_list(self, swarm: Swarm, agent: Agent) -> None:
        """Ensure agent is in swarm's agent list exactly once."""
        if not isinstance(swarm.agents, list):
            swarm.agents = []
        if agent not in swarm.agents:
            swarm.agents.append(agent)
