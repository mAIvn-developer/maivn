"""Swarm scope for coordinating multiple agents.
Provides DI-friendly registries and shared tool access for groups of agents.
"""

# pyright: strict
from __future__ import annotations

from typing import cast

from pydantic import Field, PrivateAttr, model_validator
from typing_extensions import Self, override

from maivn._internal.adapters.repositories.agent_repo import AgentRepo
from maivn._internal.core.interfaces.repositories.agent import AgentRepoInterface
from maivn._internal.core.interfaces.repositories.tool import ToolRepoInterface
from maivn._internal.core.registrars.register_agent import AgentRegistrar

from ..agent.agent import Agent, bind_agent_swarm
from ..base_scope import BaseScope
from .invocation_methods import SwarmInvocationMethodsMixin
from .member import SwarmMemberDecoratorBuilder
from .validation import validate_force_final_tool_request

# MARK: Swarm


class Swarm(SwarmInvocationMethodsMixin, BaseScope):
    """Swarm managing multiple Agents via DI and registrars.

    Orchestrator-routed methods (``invoke``/``stream``/``ainvoke``/``astream``,
    state preparation, tool registration) are provided by
    :class:`SwarmInvocationMethodsMixin`.
    """

    agents: list[Agent] = Field(
        default_factory=list,
        description="List of agents in the swarm",
    )
    agent_repo: object | None = Field(
        default=None,
        description="Optional repository for Swarm member Agent registration.",
        exclude=True,
        repr=False,
    )

    _agent_repo: AgentRepoInterface[Agent] = PrivateAttr()
    _agent_registrar: AgentRegistrar[Agent] = PrivateAttr()

    # MARK: - Lifecycle

    @override
    def model_post_init(self, context: object) -> None:
        """Initialize swarm services and registries."""
        super().model_post_init(context)

        self._agent_repo = _coerce_agent_repo(self.agent_repo)
        self._agent_registrar = AgentRegistrar(repo=self._agent_repo)

        if self.agents:
            initial_agents = list(self.agents)
            self.agents = []
            for agent in initial_agents:
                self.add_agent(agent)

    @model_validator(mode="after")
    def _validate_swarm_state(self) -> Self:
        return self

    # MARK: - Repository Access

    @property
    def member_agent_repository(self) -> AgentRepoInterface[Agent]:
        """Access the agent repository."""
        return self._agent_repo

    @property
    def member_tool_repository(self) -> ToolRepoInterface:
        """Access the tool repository."""
        return self._tool_repo

    # MARK: - Agent Management

    def add_agent(self, agent: Agent) -> None:
        """Add an agent to the swarm."""
        self._agent_registrar(self, agent)
        bind_agent_swarm(agent, self)

    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve an agent by ID."""
        return self._agent_repo.get_agent(agent_id)

    def list_agents(self) -> list[Agent]:
        """List all agents in the swarm."""
        return self._agent_repo.list_agents()

    @property
    def member(self) -> SwarmMemberDecoratorBuilder:
        """Build a decorator for registering dependency-aware Swarm member agents."""
        return SwarmMemberDecoratorBuilder(self)

    # MARK: - Validation

    @override
    def validate_on_invoke(self) -> None:
        """Validate swarm configuration before invocation."""
        self.validate_tool_configuration()
        self._ensure_has_agents()

    def _ensure_has_agents(self) -> None:
        """Ensure the swarm has at least one agent."""
        if not self.agents:
            raise ValueError("Swarm.invoke requires at least one Agent in the swarm.")

    @override
    def _validate_force_final_tool_request(self, force_final_tool: bool) -> None:
        """Validate force_final_tool usage for swarm invocations.

        Resolution order when a final response must be forced:
          1. A designated final-output agent (use_as_final_output=True) with a
             final_tool on that agent — the swarm will force that agent's tool.
          2. A designated final-output agent with no final_tool but the swarm
             has a swarm-scope final_tool — the designated agent runs and the
             swarm-scope tool supplies final output.
          3. No designated agent: fall back to a swarm-scope final_tool.
        """
        validate_force_final_tool_request(self, force_final_tool)


# MARK: - Repository Helpers


def _coerce_agent_repo(repo: object | None) -> AgentRepoInterface[Agent]:
    if repo is None:
        return AgentRepo[Agent]()

    required_methods = (
        "add_agent",
        "get_agent",
        "get_agent_by_name",
        "list_agents",
        "remove_agent",
        "update_agent",
    )
    for method_name in required_methods:
        if not callable(getattr(repo, method_name, None)):
            raise TypeError("agent_repo must implement AgentRepoInterface")
    return cast(AgentRepoInterface[Agent], repo)


def _rebuild_swarm_model() -> None:
    from ..agent import Agent

    _ = Swarm.model_rebuild(_types_namespace={"Agent": Agent})


_rebuild_swarm_model()


__all__ = ["Swarm"]
