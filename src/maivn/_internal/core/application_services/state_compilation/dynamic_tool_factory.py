"""Factory for creating dynamic tools from dependency declarations.
Builds FunctionTools for agent invocation and other dependency-driven behaviors.
Used by state compilation to augment an agent's tool list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maivn_shared import (
    AgentDependency,
    HumanMessage,
    InterruptDependency,
    create_uuid,
)

from maivn._internal.core.entities import AgentTool, BaseTool, FunctionTool
from maivn._internal.core.services.team_dependencies import (
    SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY,
    TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY,
    apply_team_invocation_signature,
    build_execution_controls_metadata,
    build_team_dependency_arg_schemas,
    build_team_dependency_context,
    format_dependency_context_for_prompt,
    get_team_dependencies,
    get_team_execution_controls,
)

from .dynamic_tool_factory_nested import DynamicToolFactoryNestedInvocationMixin
from .dynamic_tool_factory_response import DynamicToolFactoryResponseMixin

if TYPE_CHECKING:
    from maivn._internal.api.base_scope import BaseScope


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
        scope: BaseScope,
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
        scope: BaseScope,
    ) -> list[FunctionTool]:
        """Create agent invocation tools for all agents in a swarm.

        This is used during Swarm.invoke to allow the server to request
        agent invocations via TOOL_EVENT, similar to @depends_on_agent.

        Args:
            scope: The scope (Agent in a Swarm, or Swarm itself)

        Returns:
            List of agent invocation tools for all swarm members
        """
        from maivn._internal.api import Agent, Swarm

        swarm_scope: Any = None
        if isinstance(scope, Swarm):
            swarm_scope = scope
        elif isinstance(scope, Agent):
            swarm_scope = scope.get_swarm()

        if not swarm_scope or not hasattr(swarm_scope, "agents"):
            return []

        tools: list[FunctionTool] = []
        for agent in swarm_scope.agents:
            agent_id = getattr(agent, "id", None)
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
            dependencies = getattr(tool, "dependencies", None)
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
        self, agent_ids: set[str], scope: BaseScope
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

    def _create_agent_invocation_tool(self, agent_id: str, swarm_scope: Any) -> FunctionTool:
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
            model: str | None = None,
            included_nested_synthesis: bool | str | None = None,
            memory_recall_turn_active: bool = False,
            **dependency_kwargs: Any,
        ) -> Any:
            """Invoke the target agent with a prompt and return its result.

            Args:
                prompt: The prompt to send to the agent.
                use_as_final_output: If True, this agent's output is treated as
                    the swarm's final response (does not force final_tool).
                force_final_tool: If True, force execution of the agent's final_tool.
                model: LLM model selection hint ('fast', 'balanced', 'max').
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
            agent_default_nested_synthesis = getattr(agent, "included_nested_synthesis", "auto")
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

            # Nested agent responses should only stream live when the outer SDK call
            # itself is in stream mode. For invoke mode, reporter consumers receive
            # only the completed nested result.
            stream_token = allow_nested_response_stream.set(
                current_sdk_delivery_mode.get() == "stream"
            )
            try:
                response = agent.invoke(
                    messages=[HumanMessage(content=nested_prompt)],
                    force_final_tool=force_final_tool,
                    memory_config=nested_memory_config,
                    memory_assets_config=memory_assets_config,
                    swarm_config=swarm_config,
                    model=model,  # type: ignore[arg-type]
                )
            finally:
                allow_nested_response_stream.reset(stream_token)
            return self._extract_agent_response(
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
            target_agent_id=getattr(target_agent, "id", agent_id),
            metadata=metadata,
        )

    def _build_team_invocation_tool_metadata(
        self,
        *,
        team_dependencies: list[Any],
        team_execution_controls: list[Any],
        swarm_scope: Any,
    ) -> dict[str, Any]:
        """Build metadata attached to generated Swarm agent invocation tools."""
        metadata: dict[str, Any] = {}
        if team_dependencies:
            metadata[TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY] = build_team_dependency_arg_schemas(
                team_dependencies, swarm_scope
            )
            metadata[SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY] = [
                getattr(dependency, "arg_name", "")
                for dependency in team_dependencies
                if getattr(dependency, "arg_name", "")
            ]
        if team_execution_controls:
            metadata["execution_controls"] = build_execution_controls_metadata(
                team_execution_controls
            )
        return metadata

    # MARK: - Scope Resolution

    def _resolve_swarm_scope(self, scope: BaseScope) -> Any:
        """Resolve the scope to a Swarm for cross-agent communication.

        Args:
            scope: The scope (Agent or Swarm) to resolve

        Returns:
            The Swarm instance

        Raises:
            ValueError: If scope is not in a Swarm context
        """
        from maivn._internal.api import Agent, Swarm

        if isinstance(scope, Swarm):
            return scope

        if isinstance(scope, Agent):
            swarm = scope.get_swarm()
            if swarm is None:
                raise ValueError(
                    "Agent dependencies (depends_on_agent) require the agent to be part of a "
                    "Swarm. Create a Swarm and add the agent to enable cross-agent communication."
                )
            return swarm

        raise ValueError(
            "Agent dependencies (depends_on_agent) can only be used within a Swarm context. "
            "The scope must be either an Agent (part of a Swarm) or a Swarm itself."
        )

    def _find_agent_in_swarm(self, agent_id: str, swarm_scope: Any) -> Any:
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
            if getattr(agent, "id", None) == agent_id or getattr(agent, "name", None) == agent_id:
                return agent

        available = [getattr(a, "name", "unnamed") for a in swarm_scope.agents]
        raise ValueError(f"Agent '{agent_id}' not found in swarm. Available agents: {available}")

    # MARK: - Agent Metadata

    def _get_required_agent_name(self, agent: Any, agent_id: str) -> str:
        """Get the agent name, raising if not set.

        Args:
            agent: The agent instance
            agent_id: The agent ID for error messages

        Returns:
            The agent name

        Raises:
            ValueError: If agent has no name
        """
        agent_name = getattr(agent, "name", None)
        if not agent_name:
            raise ValueError(
                f"Agent with ID '{agent_id}' must have a 'name' attribute to be used with "
                f"@depends_on_agent. Please set a name when creating the Agent."
            )
        return agent_name

    def _build_tool_description(self, agent: Any, agent_name: str) -> str:
        """Build the tool description from agent metadata.

        Args:
            agent: The agent instance
            agent_name: The agent name

        Returns:
            The tool description
        """
        description = getattr(agent, "description", None)
        if description:
            return description
        return f"Invoke agent '{agent_name}' with a prompt and retrieve its result"


__all__ = ["DynamicToolFactory"]
