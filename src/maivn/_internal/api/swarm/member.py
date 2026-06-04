"""Swarm member registration builder."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypeGuard, cast

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
    from ..agent.agent import Agent

    TeamControlReference: TypeAlias = str | BaseTool | Callable[..., object] | Agent
else:
    TeamControlReference: TypeAlias = str | BaseTool | Callable[..., object]

ToolReference: TypeAlias = str | BaseTool | Callable[..., object]


# MARK: Types


class _ModelDumpable(Protocol):
    def model_dump(self, *, mode: str = "python") -> object: ...


class _SwarmMemberScope(Protocol):
    def add_agent(self, agent: Agent) -> None: ...


# MARK: Member Builder


class SwarmMemberDecoratorBuilder:
    """Builder for registering Swarm member agents with dependency metadata."""

    # MARK: - Initialization

    def __init__(self, swarm: _SwarmMemberScope) -> None:
        self._swarm: _SwarmMemberScope = swarm
        self._dependencies: list[BaseDependency] = []
        self._execution_controls: list[AwaitForDependency | ReevaluateDependency] = []

    # MARK: - Dependency Configuration

    def depends_on_tool(
        self,
        tool_ref: str | BaseTool | Callable[..., object],
        arg_name: str,
    ) -> SwarmMemberDecoratorBuilder:
        _ = depends_on_tool(tool_ref=tool_ref, arg_name=arg_name)(self)
        return self

    def depends_on_agent(
        self,
        agent_ref: str | Agent,
        arg_name: str,
    ) -> SwarmMemberDecoratorBuilder:
        _ = depends_on_agent(agent_ref=agent_ref, arg_name=arg_name)(self)
        return self

    def depends_on_await_for(
        self,
        ref: TeamControlReference,
        *,
        timing: ExecutionTiming = "after",
        instance_control: ExecutionInstanceControl = "each",
    ) -> SwarmMemberDecoratorBuilder:
        _ = depends_on_await_for(
            tool_ref=cast(ToolReference, ref),
            timing=timing,
            instance_control=instance_control,
        )(self)
        return self

    def depends_on_reevaluate(
        self,
        ref: TeamControlReference,
        *,
        timing: ExecutionTiming = "after",
        instance_control: ExecutionInstanceControl = "each",
    ) -> SwarmMemberDecoratorBuilder:
        _ = depends_on_reevaluate(
            tool_ref=cast(ToolReference, ref),
            timing=timing,
            instance_control=instance_control,
        )(self)
        return self

    def depends_on_interrupt(
        self,
        arg_name: str,
        input_handler: Callable[[str], object],
        prompt: str = "",
        input_type: InputType | None = None,
        choices: list[str] | None = None,
    ) -> SwarmMemberDecoratorBuilder:
        _ = depends_on_interrupt(
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

    def _resolve_agent(self, obj: object) -> Agent:
        from ..agent.agent import Agent

        if isinstance(obj, Agent):
            return obj

        if callable(obj):
            agent = cast(Callable[[], object], obj)()
            if isinstance(agent, Agent):
                return agent

        raise TypeError("swarm.member expects an Agent instance or a zero-argument Agent factory.")

    def _add_team_dependency(self, dependency: BaseDependency) -> None:
        """Collect dependency metadata through the public dependency decorators."""
        if isinstance(dependency, DataDependency):
            raise ValueError(
                "depends_on_private_data is not supported for Swarm member agents. "
                + "Use depends_on_private_data on a Swarm-level tool, then make the agent "
                + "depend on that tool."
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

    def _resolve_team_control_reference(self, ref: object) -> tuple[str, str]:
        resolver = cast(
            Callable[[object, object], tuple[str, str]],
            resolve_team_control_reference,
        )
        return resolver(self._swarm, ref)

    def _apply_pending_metadata(self, obj: object, agent: Agent) -> None:
        pending_dependencies = cast(list[object], getattr(obj, "_dependencies", []) or [])
        for dependency in list(pending_dependencies):
            if isinstance(dependency, BaseDependency):
                add_team_dependency(agent, dependency)

        controls = list(cast(list[object], getattr(obj, "__maivn_execution_controls__", []) or []))
        controls.extend(
            cast(list[object], getattr(obj, "__maivn_pending_execution_controls__", []) or [])
        )
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


def _contains_model(items: Sequence[object], candidate: object) -> bool:
    if not _is_model_dumpable(candidate):
        return candidate in items
    candidate_payload = candidate.model_dump(mode="json")
    for item in items:
        if _is_model_dumpable(item) and item.model_dump(mode="json") == candidate_payload:
            return True
    return False


def _is_model_dumpable(value: object) -> TypeGuard[_ModelDumpable]:
    return callable(getattr(value, "model_dump", None))


__all__ = ["SwarmMemberDecoratorBuilder"]
