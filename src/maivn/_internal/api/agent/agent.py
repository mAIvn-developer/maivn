"""Core Agent class implementation."""

# pyright: strict
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from maivn_shared import BaseDependency
from maivn_shared.domain.entities.dependencies import AwaitForDependency, ReevaluateDependency
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, PrivateAttr, field_validator
from typing_extensions import override

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.entities.tools.method_tool import MethodTool
from maivn._internal.core.interfaces import AgentOrchestratorInterface
from maivn._internal.core.orchestrator.protocols import OrchestratedSwarm
from maivn._internal.core.services.team_dependencies import (
    TeamControlReference,
    add_team_dependency,
    add_team_execution_control,
    resolve_team_control_reference,
)

from ..base_scope import BaseScope, ToolOverride
from .client_cache import get_or_create_client
from .invocation_methods import AgentInvocationMethodsMixin

if TYPE_CHECKING:
    from ..client import Client


# MARK: Types

InitialTool = BaseTool | Callable[..., object] | type[PydanticBaseModel]


# MARK: Agent


class Agent(AgentInvocationMethodsMixin, BaseScope):
    """Agent with DI-friendly construction.

    Holds api_key and SDK Client, delegates invocation to AgentOrchestrator,
    and supports swarm membership.

    Orchestrator-routed methods (``invoke``/``stream``/``ainvoke``/``astream``/
    batch/compile) are provided by :class:`AgentInvocationMethodsMixin`.
    """

    # MARK: - Fields

    api_key: str | None = Field(
        default=None,
        description="API key for server authentication.",
    )
    client: Client | None = Field(
        default=None,
        description="Optional Client; created from api_key if missing.",
    )
    timeout: float | None = Field(
        default=None,
        description="Default timeout in seconds. None uses system default.",
    )
    max_results: int | None = Field(
        default=None,
        description="Maximum tools to return from semantic search.",
    )
    use_as_final_output: bool = Field(
        default=False,
        description=(
            "When part of a Swarm invocation, force this agent's output to be the final "
            "response (only one Swarm member may set this)."
        ),
    )
    force_final_tool: bool = Field(
        default=False,
        description=(
            "When True, every invocation of this agent (including nested swarm "
            "invocations from another agent) is forced to schedule and execute its "
            "registered final_tool. Defaults to False so registering a final_tool "
            "on an agent leaves it OPTIONAL — the assignment_agent decides whether "
            "to use it. Set this to True only when the developer requires the "
            "structured final_tool output on every invocation (e.g. typed swarm "
            "handoffs that downstream agents depend on)."
        ),
    )
    included_nested_synthesis: bool | Literal["auto"] = Field(
        default="auto",
        description=("Control nested synthesis behavior for this agent when invoked by a Swarm."),
    )
    tools: list[InitialTool] = Field(
        default_factory=list,
        description="Tools registered on this agent at construction time.",
        exclude=True,
    )

    _swarm: OrchestratedSwarm | None = PrivateAttr(default=None)
    _orchestrator: AgentOrchestratorInterface | None = PrivateAttr(default=None)
    _closed: bool = PrivateAttr(default=False)
    _team_dependencies: list[BaseDependency] = PrivateAttr(default_factory=list)
    _team_execution_controls: list[AwaitForDependency | ReevaluateDependency] = PrivateAttr(
        default_factory=list
    )

    # MARK: - Properties

    @property
    def agent_id(self) -> str:
        """Unique identifier for this agent."""
        return self.id

    # MARK: - Lifecycle

    @override
    def model_post_init(self, context: object) -> None:
        """Initialize client from api_key if needed."""
        super().model_post_init(context)
        self._initialize_client()
        self._register_initial_tools()

    def _initialize_client(self) -> None:
        """Initialize client from api_key or validate existing client."""
        if self.api_key and not self.client:
            self.client = get_or_create_client(self.api_key)
        elif not self.api_key and not self.client:
            raise ValueError("Agent requires either a Client instance or an api_key.")

    @field_validator("included_nested_synthesis", mode="before")
    @classmethod
    def _normalize_included_nested_synthesis(cls, value: object) -> bool | Literal["auto"]:
        """Normalize include-nested-synthesis mode."""
        if value is None:
            return "auto"
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "auto":
                return "auto"
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("included_nested_synthesis must be True, False, or 'auto'")

    # MARK: - Tool Management

    @override
    def add_tool(
        self,
        tool: BaseTool | Callable[..., object] | type[PydanticBaseModel],
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: Callable[[dict[str, object]], object] | None = None,
        after_execute: Callable[[dict[str, object]], object] | None = None,
        override: ToolOverride | None = None,
    ) -> BaseTool:
        """Register a callable, Pydantic model, or prebuilt tool on this agent."""
        registered_tool = super().add_tool(
            tool=tool,
            name=name,
            description=description,
            always_execute=always_execute,
            final_tool=final_tool,
            tags=tags,
            before_execute=before_execute,
            after_execute=after_execute,
            override=override,
        )
        self._remember_registered_tool(registered_tool)
        return registered_tool

    @override
    def add_toolset(
        self,
        instance: object,
        *,
        include: list[str] | tuple[str, ...] | None = None,
        exclude: list[str] | tuple[str, ...] | None = None,
        include_tags: list[str] | tuple[str, ...] | None = None,
        exclude_tags: list[str] | tuple[str, ...] | None = None,
        overrides: dict[str, ToolOverride] | None = None,
    ) -> list[MethodTool]:
        """Register every ``@toolify`` method on a ``@toolset`` instance.

        Thin Agent-facing wrapper around
        :meth:`BaseScopeToolingMixin.add_toolset`. Filter kwargs
        (``include``, ``exclude``, ``include_tags``, ``exclude_tags``)
        pass through unchanged; see the scope mixin for semantics. The
        ``overrides`` map applies a per-method :class:`ToolOverride` at
        registration time (e.g. to pin a discovery tool with
        ``always_execute=True`` or attach app-specific dependencies). Each
        registered :class:`MethodTool` is also tracked on ``self.tools``
        so consumers of the existing Agent API observe the new tools the
        same way they observe ones added via :meth:`add_tool`.
        """
        registered = super().add_toolset(
            instance,
            include=include,
            exclude=exclude,
            include_tags=include_tags,
            exclude_tags=exclude_tags,
            overrides=overrides,
        )
        for tool in registered:
            self._remember_registered_tool(tool)
        return registered

    def _register_initial_tools(self) -> None:
        initial_tools = list(self.tools)
        self.tools = []
        for tool in initial_tools:
            _ = self.add_tool(tool)

    def _remember_registered_tool(self, tool: BaseTool) -> None:
        tool_id = tool.tool_id
        for registered_tool in self.tools:
            if not isinstance(registered_tool, BaseTool):
                continue
            if registered_tool.tool_id == tool_id:
                return
            if registered_tool is tool:
                return
        self.tools.append(tool)

    # MARK: - Swarm

    def get_swarm(self) -> OrchestratedSwarm | None:
        """Get parent swarm if agent belongs to one."""
        return self._swarm

    def _add_team_dependency(self, dependency: BaseDependency) -> None:
        """Attach dependency metadata for Swarm team invocation."""
        add_team_dependency(self, dependency)

    def _add_team_execution_control(
        self,
        control: AwaitForDependency | ReevaluateDependency,
    ) -> None:
        """Attach execution-control metadata for Swarm team invocation."""
        add_team_execution_control(self, control)

    def _resolve_team_control_reference(self, ref: TeamControlReference) -> tuple[str, str]:
        """Resolve Swarm agent/tool refs for team execution-control decorators."""
        swarm = self.get_swarm()
        if swarm is None:
            raise ValueError(
                "Team execution controls require the agent to be registered with a Swarm."
            )
        return resolve_team_control_reference(swarm, ref)

    # MARK: - Cleanup

    def close(self) -> None:
        """Release underlying orchestrator resources."""
        if self._closed:
            return
        self._closed = True
        try:
            self.close_mcp_servers()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
        orchestrator = self._orchestrator
        if orchestrator is None:
            return
        try:
            orchestrator.close()
        except (RuntimeError, OSError, AttributeError):
            pass

    def __del__(self) -> None:
        """Cleanup on garbage collection."""
        if sys.is_finalizing():
            return
        try:
            self.close()
        except Exception:  # noqa: BLE001 - __del__ must never raise
            pass


def bind_agent_swarm(agent: Agent, swarm: OrchestratedSwarm) -> None:
    """Bind an agent to its parent swarm (framework-internal).

    Centralizes the single private back-reference write the swarm-registration flow
    performs. It is a module function -- not a public ``Agent`` method -- so it stays
    off the developer-facing API: application code only ever *reads* an agent's swarm,
    via :meth:`Agent.get_swarm`. The dynamic ``__setattr__`` keeps ``_swarm`` private
    (a normal assignment from outside the class is rejected by the type checker) while
    still routing through Pydantic's private-attr store.
    """
    agent.__setattr__("_swarm", swarm)


def _rebuild_agent_model() -> None:
    from ..client import Client

    _ = Agent.model_rebuild(_types_namespace={"Client": Client})


_rebuild_agent_model()
