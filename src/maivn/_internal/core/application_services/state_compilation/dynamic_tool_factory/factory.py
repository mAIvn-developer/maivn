"""Factory for creating dynamic tools from dependency declarations.
Builds FunctionTools for agent invocation and other dependency-driven behaviors.
Used by state compilation to augment an agent's tool list."""

# pyright: strict
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol, TypeAlias, TypeGuard, cast

from maivn_shared import (
    FINAL_EVENT_NAME,
    UPDATE_EVENT_NAME,
    AgentDependency,
    BaseDependency,
    HumanMessage,
    InterruptDependency,
    SessionResponse,
    create_uuid,
)
from maivn_shared.domain.entities.session_config import NestedSynthesisMode

from maivn._internal.core.entities import AgentTool, BaseTool, FunctionTool, SSEEvent
from maivn._internal.core.services.team_dependencies import (
    SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY,
    TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY,
    TeamExecutionControl,
    apply_team_invocation_signature,
    build_execution_controls_metadata,
    build_team_dependency_arg_schemas,
    build_team_dependency_context,
    format_dependency_context_for_prompt,
    get_team_dependencies,
    get_team_execution_controls,
)

from .nested import DynamicToolFactoryNestedInvocationMixin
from .response import DynamicToolFactoryResponseMixin

# MARK: - Types

ModelSelection: TypeAlias = Literal["fast", "balanced", "max", "ultra"]

logger = logging.getLogger(__name__)
_NESTED_FINAL_RESPONSE_ASSISTANT_IDS = frozenset({"chat_agent", "orchestrator_agent"})


class DynamicInvocationAgent(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def description(self) -> str | None: ...

    @property
    def included_nested_synthesis(self) -> bool | Literal["auto"]: ...

    @property
    def force_final_tool(self) -> bool: ...

    def invoke(self, **kwargs: object) -> object: ...
    def stream(self, **kwargs: object) -> Iterable[object]: ...

    def get_swarm(self) -> DynamicInvocationSwarm | None: ...


class DynamicInvocationSwarm(Protocol):
    @property
    def agents(self) -> Sequence[DynamicInvocationAgent]: ...

    def list_tools(self) -> Sequence[BaseTool]: ...


class DynamicToolFactory(
    DynamicToolFactoryNestedInvocationMixin,
    DynamicToolFactoryResponseMixin,
):
    """Creates dynamic tools for agent and user dependencies.

    This factory extracts the responsibility of creating dynamic tools from
    StateCompiler, following the Single Responsibility Principle.
    """

    # MARK: - Public API

    def create_dependency_tools(
        self,
        tools: list[BaseTool],
        scope: object,
    ) -> tuple[list[FunctionTool], list[FunctionTool]]:
        """Create dynamic tools for all dependencies in the tool list.

        Args:
            tools: List of tools to extract dependencies from
            scope: Scope (Agent or Swarm) for context

        Returns:
            Tuple of (agent_tools, user_tools)
        """
        agent_ids, _ = self._extract_dependencies(tools)
        agent_tools = self._create_agent_invocation_tools(agent_ids, scope)
        return agent_tools, []

    def create_swarm_agent_invocation_tools(
        self,
        scope: object,
    ) -> list[FunctionTool]:
        """Create agent invocation tools for all agents in a swarm.

        This is used during Swarm.invoke to allow the server to request
        agent invocations via TOOL_EVENT, similar to @depends_on_agent.

        Args:
            scope: The scope (Agent in a Swarm, or Swarm itself)

        Returns:
            List of agent invocation tools for all swarm members
        """
        swarm_scope: DynamicInvocationSwarm | None = None
        if _is_dynamic_invocation_swarm(scope):
            swarm_scope = scope
        elif _is_dynamic_invocation_agent(scope):
            swarm_scope = scope.get_swarm()

        if swarm_scope is None:
            return []

        tools: list[FunctionTool] = []
        for agent in swarm_scope.agents:
            agent_id = agent.id
            if agent_id:
                tool = self._create_agent_invocation_tool(agent_id, swarm_scope)
                tools.append(tool)

        return tools

    # MARK: - Dependency Extraction

    def _extract_dependencies(
        self, tools: list[BaseTool]
    ) -> tuple[set[str], list[InterruptDependency]]:
        """Extract agent and user dependencies in a single pass.

        Args:
            tools: List of tools to extract dependencies from

        Returns:
            Tuple of (agent_ids, user_dependencies)
        """
        agent_ids: set[str] = set()
        user_dependencies: list[InterruptDependency] = []

        for tool in tools:
            dependencies = tool.dependencies
            if not dependencies:
                continue
            for dep in dependencies:
                if isinstance(dep, AgentDependency):
                    agent_ids.add(dep.agent_id)
                elif isinstance(dep, InterruptDependency):
                    user_dependencies.append(dep)

        return agent_ids, user_dependencies

    # MARK: - Agent Tool Creation

    def _create_agent_invocation_tools(
        self, agent_ids: set[str], scope: object
    ) -> list[FunctionTool]:
        """Create dynamic agent invocation tools for each agent dependency.

        Args:
            agent_ids: Set of agent IDs that need dynamic invocation tools
            scope: The scope (Agent or Swarm) in which tools are being compiled

        Returns:
            List of FunctionTool instances for agent invocation

        Raises:
            ValueError: If scope is not in a Swarm context
        """
        if not agent_ids:
            return []

        swarm_scope = self._resolve_swarm_scope(scope)
        return [self._create_agent_invocation_tool(agent_id, swarm_scope) for agent_id in agent_ids]

    def _create_agent_invocation_tool(
        self,
        agent_id: str,
        swarm_scope: DynamicInvocationSwarm,
    ) -> FunctionTool:
        """Create a single dynamic agent invocation tool.

        Args:
            agent_id: The ID of the agent to create an invocation tool for
            swarm_scope: The Swarm containing the target agent

        Returns:
            FunctionTool for invoking the target agent
        """
        target_agent = self._find_agent_in_swarm(agent_id, swarm_scope)
        agent_name = self._get_required_agent_name(target_agent, agent_id)
        tool_description = self._build_tool_description(target_agent, agent_name)
        team_dependencies = get_team_dependencies(target_agent)
        team_execution_controls = get_team_execution_controls(target_agent)

        def invoke_agent(
            prompt: str,
            use_as_final_output: bool = False,
            force_final_tool: bool = False,
            model: ModelSelection | None = None,
            included_nested_synthesis: NestedSynthesisMode | str | None = None,
            memory_recall_turn_active: bool = False,
            **dependency_kwargs: object,
        ) -> object:
            """Invoke the target agent with a prompt and return its result.

            Args:
                prompt: The prompt to send to the agent.
                use_as_final_output: If True, this agent's output is treated as
                    the swarm's final response (does not force final_tool).
                force_final_tool: If True, force execution of the agent's final_tool.
                model: LLM model selection hint ('fast', 'balanced', 'max', 'ultra').
                    Use 'fast' for intermediate agents to maximize performance.
                included_nested_synthesis: Nested synthesis mode for the invoked agent.
                    True=always include synthesized response, False=skip synthesis,
                    "auto"=let swarm orchestration/runtime decide.
                memory_recall_turn_active: If True, mark the nested invocation as
                    strict recall active for this turn.
            """
            agent = self._find_agent_in_swarm(agent_id, swarm_scope)
            dependency_context = build_team_dependency_context(
                dependency_kwargs,
                team_dependencies,
            )
            nested_prompt = format_dependency_context_for_prompt(prompt, dependency_context)
            agent_default_nested_synthesis = agent.included_nested_synthesis
            resolved_nested_synthesis = self._normalize_included_nested_synthesis(
                included_nested_synthesis
                if included_nested_synthesis is not None
                else agent_default_nested_synthesis
            )
            swarm_config = self._build_nested_invocation_swarm_config(
                agent=agent,
                agent_id=agent_id,
                use_as_final_output=use_as_final_output,
                resolved_nested_synthesis=resolved_nested_synthesis,
            )
            if dependency_context:
                swarm_config = swarm_config.model_copy(
                    update={
                        "agent_dependency_context": dependency_context,
                        "agent_dependency_context_keys": list(dependency_context),
                    }
                )
            memory_assets_config = self._build_nested_invocation_memory_assets_config(
                agent=agent,
                swarm_scope=swarm_scope,
                memory_recall_turn_active=memory_recall_turn_active,
            )
            nested_memory_config = self._build_nested_invocation_memory_config(
                agent=agent,
                swarm_scope=swarm_scope,
            )

            from maivn._internal.utils.reporting.context import (
                allow_nested_response_stream,
                current_sdk_delivery_mode,
            )

            # Nested agent responses should only stream live when this nested call
            # is explicitly serving as the final user-facing output.
            stream_token = allow_nested_response_stream.set(
                use_as_final_output and current_sdk_delivery_mode.get() == "stream"
            )
            # Honor explicit per-agent opt-in via `agent.force_final_tool`. The
            # caller's `force_final_tool` argument always wins; otherwise we fall
            # back to the agent's developer-declared default. Agents that
            # register a final_tool intentionally remain optional (the
            # assignment_agent decides when to use them) unless the developer
            # marks the agent itself as `force_final_tool=True` to require it.
            agent_default_force_final_tool = bool(agent.force_final_tool)
            effective_force_final_tool = force_final_tool or agent_default_force_final_tool
            if (
                effective_force_final_tool
                and not force_final_tool
                and agent_default_force_final_tool
            ):
                logger.debug(
                    "[DYNAMIC_TOOL_FACTORY] Honoring agent.force_final_tool=True "
                    + "for nested swarm agent '%s' (developer-declared default).",
                    agent_name,
                )
            try:
                invocation_kwargs: dict[str, object] = {
                    "messages": [HumanMessage(content=nested_prompt)],
                    "force_final_tool": effective_force_final_tool,
                    "memory_config": nested_memory_config,
                    "memory_assets_config": memory_assets_config,
                    "swarm_config": swarm_config,
                    "model": model,
                }
                if current_sdk_delivery_mode.get() == "stream":
                    response = self._consume_nested_agent_stream(
                        agent.stream(**invocation_kwargs),
                        agent_id=agent_id,
                    )
                else:
                    response = agent.invoke(**invocation_kwargs)
            finally:
                allow_nested_response_stream.reset(stream_token)
            return self.extract_agent_response(
                response,
                agent_id,
                include_response=True,
            )

        if team_dependencies:
            apply_team_invocation_signature(invoke_agent, team_dependencies)

        metadata = self._build_team_invocation_tool_metadata(
            team_dependencies=team_dependencies,
            team_execution_controls=team_execution_controls,
            swarm_scope=swarm_scope,
        )

        return AgentTool(
            tool_id=create_uuid(f"agent_invoke_{agent_id}"),
            name=agent_name,
            description=tool_description,
            func=invoke_agent,
            tags=["dynamic", "agent_invocation"],
            target_agent_id=target_agent.id,
            metadata=metadata,
        )

    def _consume_nested_agent_stream(
        self,
        events: Iterable[object],
        *,
        agent_id: str,
    ) -> SessionResponse:
        """Consume a nested streamed agent run and return its terminal response."""
        final_payload: object | None = None
        streamed_live = False
        for event in events:
            if getattr(event, "name", None) != FINAL_EVENT_NAME:
                streamed_live = self._forward_nested_streaming_update(event) or streamed_live
                continue
            final_payload = getattr(event, "payload", None)

        if not isinstance(final_payload, dict):
            raise RuntimeError(
                f"Nested streamed agent '{agent_id}' completed without final payload"
            )
        response = SessionResponse.model_validate(final_payload)
        if not streamed_live:
            return response

        metadata = dict(response.metadata or {})
        metadata["response_streamed_live"] = True
        return response.model_copy(update={"metadata": metadata})

    @staticmethod
    def _forward_nested_streaming_update(event: object) -> bool:
        """Forward nested assistant stream chunks into the active outer stream."""
        if not isinstance(event, SSEEvent):
            return False
        if getattr(event, "name", None) != UPDATE_EVENT_NAME:
            return False
        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            return False
        payload_dict = cast(dict[str, object], payload)
        streaming_content = payload_dict.get("streaming_content")
        if not isinstance(streaming_content, str) or not streaming_content:
            return False
        if not DynamicToolFactory._is_nested_final_response_assistant(payload_dict):
            return False

        from maivn._internal.utils.reporting.context import (
            allow_nested_response_stream,
            current_stream_event_forwarder,
        )

        if not allow_nested_response_stream.get():
            return False
        forwarder = current_stream_event_forwarder.get()
        if forwarder is None:
            return False
        forwarder(event)
        return True

    @staticmethod
    def _is_nested_final_response_assistant(payload: dict[str, object]) -> bool:
        assistant_id = payload.get("assistant_id")
        if not isinstance(assistant_id, str):
            return False
        return assistant_id.strip() in _NESTED_FINAL_RESPONSE_ASSISTANT_IDS

    def _build_team_invocation_tool_metadata(
        self,
        *,
        team_dependencies: list[BaseDependency],
        team_execution_controls: list[TeamExecutionControl],
        swarm_scope: DynamicInvocationSwarm,
    ) -> dict[str, object]:
        """Build metadata attached to generated Swarm agent invocation tools."""
        metadata: dict[str, object] = {}
        if team_dependencies:
            metadata[TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY] = build_team_dependency_arg_schemas(
                team_dependencies, swarm_scope
            )
            metadata[SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY] = [
                dependency.arg_name for dependency in team_dependencies if dependency.arg_name
            ]
        if team_execution_controls:
            metadata["execution_controls"] = build_execution_controls_metadata(
                team_execution_controls
            )
        return metadata

    # MARK: - Scope Resolution

    def _resolve_swarm_scope(self, scope: object) -> DynamicInvocationSwarm:
        """Resolve the scope to a Swarm for cross-agent communication.

        Args:
            scope: The scope (Agent or Swarm) to resolve

        Returns:
            The Swarm instance

        Raises:
            ValueError: If scope is not in a Swarm context
        """
        if _is_dynamic_invocation_swarm(scope):
            return scope

        if _is_dynamic_invocation_agent(scope):
            swarm = scope.get_swarm()
            if swarm is None:
                raise ValueError(
                    "Agent dependencies (depends_on_agent) require the agent to be part of a "
                    + "Swarm. Create a Swarm and add the agent to enable cross-agent communication."
                )
            return swarm

        raise ValueError(
            "Agent dependencies (depends_on_agent) can only be used within a Swarm context. "
            + "The scope must be either an Agent (part of a Swarm) or a Swarm itself."
        )

    def _find_agent_in_swarm(
        self,
        agent_id: str,
        swarm_scope: DynamicInvocationSwarm,
    ) -> DynamicInvocationAgent:
        """Find an agent in the swarm by ID or name.

        Args:
            agent_id: The agent ID or name to search for
            swarm_scope: The Swarm to search in

        Returns:
            The Agent instance

        Raises:
            ValueError: If agent is not found
        """
        for agent in swarm_scope.agents:
            if agent.id == agent_id or agent.name == agent_id:
                return agent

        available = [agent.name or "unnamed" for agent in swarm_scope.agents]
        raise ValueError(f"Agent '{agent_id}' not found in swarm. Available agents: {available}")

    # MARK: - Agent Metadata

    def _get_required_agent_name(self, agent: DynamicInvocationAgent, agent_id: str) -> str:
        """Get the agent name, raising if not set.

        Args:
            agent: The agent instance
            agent_id: The agent ID for error messages

        Returns:
            The agent name

        Raises:
            ValueError: If agent has no name
        """
        agent_name = agent.name
        if not agent_name:
            raise ValueError(
                f"Agent with ID '{agent_id}' must have a 'name' attribute to be used with "
                + "@depends_on_agent. Please set a name when creating the Agent."
            )
        return agent_name

    def _build_tool_description(self, agent: DynamicInvocationAgent, agent_name: str) -> str:
        """Build the tool description from agent metadata.

        Args:
            agent: The agent instance
            agent_name: The agent name

        Returns:
            The tool description
        """
        description = agent.description
        if description:
            return description
        return f"Invoke agent '{agent_name}' with a prompt and retrieve its result"


# MARK: - Runtime Type Guards


def _is_dynamic_invocation_swarm(value: object) -> TypeGuard[DynamicInvocationSwarm]:
    return isinstance(getattr(value, "agents", None), list) and callable(
        getattr(value, "list_tools", None)
    )


def _is_dynamic_invocation_agent(value: object) -> TypeGuard[DynamicInvocationAgent]:
    return callable(getattr(value, "get_swarm", None)) and callable(getattr(value, "invoke", None))


__all__ = ["DynamicToolFactory"]
