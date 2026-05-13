"""Core Agent class implementation."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from maivn_shared import BaseDependency
from maivn_shared.domain.entities.dependencies import AwaitForDependency, ReevaluateDependency
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field, PrivateAttr, field_validator

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces import AgentOrchestratorInterface
from maivn._internal.core.services.team_dependencies import (
    add_team_dependency,
    add_team_execution_control,
    resolve_team_control_reference,
)

from ..base_scope import BaseScope
from .client_cache import get_or_create_client
from .invocation_methods import AgentInvocationMethodsMixin

if TYPE_CHECKING:
    from ..client import Client
    from ..swarm import Swarm


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
    tools: list[Any] = Field(
        default_factory=list,
        description="Tools registered on this agent at construction time.",
        exclude=True,
    )

    _swarm: Swarm | None = PrivateAttr(default=None)
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

    def model_post_init(self, context: Any) -> None:
        """Initialize client from api_key if needed."""
        super().model_post_init(context)
        self._initialize_client()
        self._register_initial_tools()

    def _initialize_client(self) -> None:
        """Initialize client from api_key or validate existing client."""
        if self.api_key and not self.client:
            self.client = self._get_or_create_client(self.api_key)
        elif not self.api_key and not self.client:
            raise ValueError("Agent requires either a Client instance or an api_key.")

    @field_validator("included_nested_synthesis", mode="before")
    @classmethod
    def _normalize_included_nested_synthesis(cls, value: Any) -> bool | Literal["auto"]:
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

    @staticmethod
    def _get_or_create_client(api_key: str) -> Client:
        """Get cached client or create new one."""
        return get_or_create_client(api_key)

    # MARK: - Tool Management

    def add_tool(
        self,
        tool: BaseTool | Callable[..., Any] | type[PydanticBaseModel],
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: Callable[[dict[str, Any]], Any] | None = None,
        after_execute: Callable[[dict[str, Any]], Any] | None = None,
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
        )
        self._remember_registered_tool(registered_tool)
        return registered_tool

    def _register_initial_tools(self) -> None:
        initial_tools = list(self.tools)
        self.tools = []
        for tool in initial_tools:
            self.add_tool(tool)

    def _remember_registered_tool(self, tool: BaseTool) -> None:
        tool_id = getattr(tool, "tool_id", None)
        for registered_tool in self.tools:
            if tool_id is not None and getattr(registered_tool, "tool_id", None) == tool_id:
                return
            if registered_tool is tool:
                return
        self.tools.append(tool)

    # MARK: - Swarm

    def get_swarm(self) -> Swarm | None:
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

    def _resolve_team_control_reference(self, ref: Any) -> tuple[str, str]:
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
        orchestrator = getattr(self, "_orchestrator", None)
        if orchestrator is None:
            return
        if not hasattr(orchestrator, "close"):
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


def _rebuild_agent_model() -> None:
    from ..client import Client

    Agent.model_rebuild(_types_namespace={"Client": Client})


_rebuild_agent_model()
