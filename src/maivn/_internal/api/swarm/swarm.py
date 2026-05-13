"""Swarm scope for coordinating multiple agents.
Provides DI-friendly registries and shared tool access for groups of agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field, PrivateAttr, model_validator
from typing_extensions import Self

from maivn._internal.adapters.repositories import AgentRepo
from maivn._internal.core.interfaces.repositories import AgentRepoInterface, ToolRepoInterface
from maivn._internal.core.registrars import AgentRegistrar

from ..base_scope import BaseScope
from .invocation_methods import SwarmInvocationMethodsMixin
from .member import SwarmMemberDecoratorBuilder
from .validation import validate_force_final_tool_request

if TYPE_CHECKING:
    from ..agent import Agent


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

    _agent_repo: AgentRepoInterface = PrivateAttr()
    _agent_registrar: AgentRegistrar = PrivateAttr()

    # MARK: - Lifecycle

    def model_post_init(self, context: Any) -> None:
        """Initialize swarm services and registries."""
        super().model_post_init(context)

        self._agent_repo = getattr(self, "agent_repo", None) or AgentRepo()
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
    def member_agent_repository(self) -> AgentRepoInterface:
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

    def get_agent(self, agent_id: str) -> Agent | None:
        """Retrieve an agent by ID."""
        return self._agent_repo.get_agent(agent_id)  # type: ignore[return-value]

    def list_agents(self) -> list[Agent]:
        """List all agents in the swarm."""
        return self._agent_repo.list_agents()  # type: ignore[return-value]

    @property
    def member(self) -> SwarmMemberDecoratorBuilder:
        """Build a decorator for registering dependency-aware Swarm member agents."""
        return SwarmMemberDecoratorBuilder(self)

    # MARK: - Validation

    def validate_on_invoke(self) -> None:
        """Validate swarm configuration before invocation."""
        self.validate_tool_configuration()
        self._ensure_has_agents()

    def _ensure_has_agents(self) -> None:
        """Ensure the swarm has at least one agent."""
        if not self.agents:
            raise ValueError("Swarm.invoke requires at least one Agent in the swarm.")

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


def _rebuild_swarm_model() -> None:
    from ..agent import Agent

    Swarm.model_rebuild(_types_namespace={"Agent": Agent})


_rebuild_swarm_model()


__all__ = ["Swarm"]
