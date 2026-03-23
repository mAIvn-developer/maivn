"""In-memory agent repository implementation.
Stores agents by id and maintains a secondary name index for lookup.
"""

from __future__ import annotations

# MARK: In-Memory Agent Repository
from typing import TYPE_CHECKING

from maivn._internal.core.interfaces.repositories import AgentRepoInterface

if TYPE_CHECKING:
    from maivn._internal.api.agent import Agent


class AgentRepo(AgentRepoInterface):
    """In-memory implementation of AgentRepoInterface."""

    def __init__(self) -> None:
        self.store: dict[str, Agent] = {}
        self._name_index: dict[str, Agent] = {}

    # MARK: - Private methods

    def _get_agent_id(self, agent: Agent) -> str | None:
        return getattr(agent, "id", None)

    def _get_agent_name(self, agent: Agent) -> str | None:
        return getattr(agent, "name", None)

    def _add_to_name_index(self, agent: Agent) -> None:
        name = self._get_agent_name(agent)
        if name:
            self._name_index[name] = agent

    def _remove_from_name_index(self, agent: Agent) -> None:
        name = self._get_agent_name(agent)
        if name and name in self._name_index:
            del self._name_index[name]

    # MARK: - Agent methods

    def add_agent(self, agent: Agent) -> None:
        if agent is None:
            return
        agent_id = self._get_agent_id(agent)
        if not agent_id or agent_id in self.store:
            return
        self.store[agent_id] = agent
        self._add_to_name_index(agent)

    def get_agent(self, agent_id: str) -> Agent | None:
        return self.store.get(agent_id)

    def get_agent_by_name(self, name: str) -> Agent | None:
        return self._name_index.get(name)

    def list_agents(self) -> list[Agent]:
        return list(self.store.values())

    def remove_agent(self, agent_id: str) -> None:
        agent = self.store.pop(agent_id, None)
        if agent:
            self._remove_from_name_index(agent)

    def update_agent(self, agent: Agent) -> None:
        agent_id = self._get_agent_id(agent)
        if not agent_id:
            return
        if agent_id in self.store:
            self._remove_from_name_index(self.store[agent_id])
        self.store[agent_id] = agent
        self._add_to_name_index(agent)
