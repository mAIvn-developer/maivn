"""Swarm member registration builder."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maivn_shared import AgentDependency, BaseDependency, DataDependency
from maivn_shared.domain.entities.dependencies import (
    AwaitForDependency,
    ExecutionInstanceControl,
    ExecutionTiming,
    InputType,
    ReevaluateDependency,
)

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.services.team_dependencies import (
    add_team_dependency,
    add_team_execution_control,
    get_team_dependencies,
    resolve_team_control_reference,
)
from maivn._internal.utils.decorators import (
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_reevaluate,
    depends_on_tool,
)

if TYPE_CHECKING:
    from ..agent import Agent
    from .swarm import Swarm


# MARK: Member Builder


class SwarmMemberDecoratorBuilder:
    """Builder for registering Swarm member agents with dependency metadata."""

    # MARK: - Initialization

    def __init__(self, swarm: Swarm) -> None:
        self._swarm = swarm
        self._dependencies: list[BaseDependency] = []
        self._execution_controls: list[AwaitForDependency | ReevaluateDependency] = []

    # MARK: - Dependency Configuration

    def depends_on_tool(
        self,
        tool_ref: str | BaseTool | Callable[..., Any],
        arg_name: str,
    ) -> SwarmMemberDecoratorBuilder:
        depends_on_tool(tool_ref=tool_ref, arg_name=arg_name)(self)
        return self

    def depends_on_agent(
        self,
        agent_ref: str | Any,
        arg_name: str,
    ) -> SwarmMemberDecoratorBuilder:
        depends_on_agent(agent_ref=agent_ref, arg_name=arg_name)(self)
        return self

    def depends_on_await_for(
        self,
        ref: str | BaseTool | Callable[..., Any] | Any,
        *,
        timing: ExecutionTiming = "after",
        instance_control: ExecutionInstanceControl = "each",
    ) -> SwarmMemberDecoratorBuilder:
        depends_on_await_for(
            tool_ref=ref,
            timing=timing,
            instance_control=instance_control,
        )(self)
        return self

    def depends_on_reevaluate(
        self,
        ref: str | BaseTool | Callable[..., Any] | Any,
        *,
        timing: ExecutionTiming = "after",
        instance_control: ExecutionInstanceControl = "each",
    ) -> SwarmMemberDecoratorBuilder:
        depends_on_reevaluate(
            tool_ref=ref,
            timing=timing,
            instance_control=instance_control,
        )(self)
        return self

    def depends_on_interrupt(
        self,
        arg_name: str,
        input_handler: Callable[[str], Any],
        prompt: str = "",
        input_type: InputType | None = None,
        choices: list[str] | None = None,
    ) -> SwarmMemberDecoratorBuilder:
        depends_on_interrupt(
            arg_name=arg_name,
            input_handler=input_handler,
            prompt=prompt,
            input_type=input_type,
            choices=choices,
        )(self)
        return self

    # MARK: - Invocation

    def __call__(
        self,
        obj: Agent | Callable[..., Agent] | None = None,
    ) -> Agent | SwarmMemberDecoratorBuilder:
        if obj is None:
            return self

        agent = self._resolve_agent(obj)
        self._apply_pending_metadata(obj, agent)
        self._apply_builder_metadata(agent)
        self._validate_no_self_agent_dependency(agent)
        self._swarm.add_agent(agent)
        return agent

    # MARK: - Private Helpers

    def _resolve_agent(self, obj: Agent | Callable[..., Agent]) -> Agent:
        from ..agent import Agent

        if isinstance(obj, Agent):
            return obj

        if callable(obj):
            agent = obj()
            if isinstance(agent, Agent):
                return agent

        raise TypeError("swarm.member expects an Agent instance or a zero-argument Agent factory.")

    def _add_team_dependency(self, dependency: BaseDependency) -> None:
        """Collect dependency metadata through the public dependency decorators."""
        if isinstance(dependency, DataDependency):
            raise ValueError(
                "depends_on_private_data is not supported for Swarm member agents. "
                "Use depends_on_private_data on a Swarm-level tool, then make the agent "
                "depend on that tool."
            )
        if not _contains_model(self._dependencies, dependency):
            self._dependencies.append(dependency)

    def _add_team_execution_control(
        self,
        control: AwaitForDependency | ReevaluateDependency,
    ) -> None:
        """Collect execution-control metadata through public dependency decorators."""
        if not _contains_model(self._execution_controls, control):
            self._execution_controls.append(control)

    def _resolve_team_control_reference(self, ref: Any) -> tuple[str, str]:
        return resolve_team_control_reference(self._swarm, ref)

    def _apply_pending_metadata(self, obj: Any, agent: Agent) -> None:
        for dependency in list(getattr(obj, "_dependencies", []) or []):
            add_team_dependency(agent, dependency)

        controls = list(getattr(obj, "__maivn_execution_controls__", []) or [])
        controls.extend(getattr(obj, "__maivn_pending_execution_controls__", []) or [])
        for control in controls:
            if isinstance(control, AwaitForDependency | ReevaluateDependency):
                add_team_execution_control(agent, control)

    def _apply_builder_metadata(self, agent: Agent) -> None:
        for dependency in self._dependencies:
            add_team_dependency(agent, dependency)
        for control in self._execution_controls:
            add_team_execution_control(agent, control)

    def _validate_no_self_agent_dependency(self, agent: Agent) -> None:
        identifiers = {getattr(agent, "id", None), getattr(agent, "name", None)}
        for dependency in get_team_dependencies(agent):
            if isinstance(dependency, AgentDependency) and dependency.agent_id in identifiers:
                raise ValueError("Swarm member agents cannot depend_on_agent themselves.")


def _contains_model(items: list[Any], candidate: Any) -> bool:
    if not hasattr(candidate, "model_dump"):
        return candidate in items
    candidate_payload = candidate.model_dump(mode="json")
    for item in items:
        if hasattr(item, "model_dump") and item.model_dump(mode="json") == candidate_payload:
            return True
    return False


__all__ = ["SwarmMemberDecoratorBuilder"]
