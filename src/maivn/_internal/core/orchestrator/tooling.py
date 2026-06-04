"""Tool index and mapping helpers for AgentOrchestrator."""

# pyright: strict
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Protocol, TypeAlias, cast, runtime_checkable

from maivn._internal.core.entities import (
    AgentTool,
    BaseTool,
    FunctionTool,
    McpTool,
    MethodTool,
    ModelTool,
)

from .protocols import OrchestratedAgent, OrchestratedSwarm

logger = logging.getLogger(__name__)


# MARK: Types

RuntimeTool: TypeAlias = FunctionTool | MethodTool | ModelTool | McpTool


class _ToolExecutionIndex(Protocol):
    def rebuild_index(self, tools: list[RuntimeTool]) -> None: ...

    def resolve_tool(self, tool_id: str) -> RuntimeTool: ...


class _ToolExecutionOrchestrator(Protocol):
    def clear_results(self) -> None: ...


class _StateCompilerDynamicTools(Protocol):
    _dynamic_tools: list[FunctionTool]


@runtime_checkable
class _AgentInvocationTool(Protocol):
    tool_type: str
    target_agent_id: str | None


# MARK: - Tool Index Coordinator


class ToolIndexCoordinator:
    """Track tool indexing and agent mapping for orchestration."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        tool_execution: _ToolExecutionIndex,
        tool_exec_orchestrator: _ToolExecutionOrchestrator,
        state_compiler: _StateCompilerDynamicTools,
        agent: OrchestratedAgent,
    ) -> None:
        self._tool_execution: _ToolExecutionIndex = tool_execution
        self._tool_exec_orchestrator: _ToolExecutionOrchestrator = tool_exec_orchestrator
        self._state_compiler: _StateCompilerDynamicTools = state_compiler
        self._agent: OrchestratedAgent = agent
        self._tool_to_agent_map: dict[str, str] = {}
        self._agent_count: int = 1

    # MARK: - Accessors

    @property
    def agent_count(self) -> int:
        return self._agent_count

    def get_agent_count(self) -> int:
        return self._agent_count

    def tool_agent_lookup(self, key: str) -> str | None:
        result = self._tool_to_agent_map.get(key)
        if result is None:
            # Fallback to current agent's name when tool mapping is not found.
            # This handles nested agent invocations where the nested orchestrator
            # may not have full tool-agent mapping from parent swarm context.
            fallback_name = self._agent.name
            if fallback_name:
                message = (
                    "[TOOLING] tool_agent_lookup(%r) -> None in map, "
                    + "falling back to current agent: %r"
                )
                logger.debug(
                    message,
                    key,
                    fallback_name,
                )
                return fallback_name
            logger.debug(
                "[TOOLING] tool_agent_lookup(%r) -> None. Known keys: %s",
                key,
                list(self._tool_to_agent_map.keys()),
            )
        return result

    # MARK: - Index Management

    def rebuild_tool_index(self, tools: Sequence[RuntimeTool]) -> None:
        self._tool_execution.rebuild_index(list(tools))
        self._tool_exec_orchestrator.clear_results()

    def rebuild_tool_index_with_dynamic_tools(self, base_tools: Sequence[BaseTool]) -> None:
        dynamic_tools = self._get_dynamic_tools()
        if dynamic_tools:
            self.rebuild_tool_index(cast(list[RuntimeTool], list(base_tools) + dynamic_tools))

    def build_tool_agent_mapping(self, swarm: OrchestratedSwarm | None) -> None:
        self._tool_to_agent_map.clear()
        if swarm:
            agents = swarm.agents
            self._agent_count = len(agents) if agents else 1
            for agent in agents:
                self._map_tools_to_agent(agent.list_tools(), agent.name or "agent")
            for tool in self._get_dynamic_tools():
                self._register_tool_mapping(tool, self._agent.name or "agent")
        else:
            self._agent_count = 1
            self._map_tools_to_agent(self._agent.list_tools(), self._agent.name or "agent")

    def register_swarm_agent_tools(self, agent_tools: Sequence[AgentTool]) -> None:
        for tool in agent_tools:
            if self._is_duplicate_agent_invocation(tool):
                agent_name = tool.name
                if agent_name:
                    self._register_tool_mapping(tool, agent_name)
                else:
                    self._register_tool_mapping(tool, self._agent.name or "agent")
                continue

            self._register_tool(tool)
            # Map invocation tools to the target agent name for accurate UI grouping.
            # AgentTool names are set to the target agent's name.
            agent_name = tool.name
            if agent_name:
                self._register_tool_mapping(tool, agent_name)
            else:
                self._register_tool_mapping(tool, self._agent.name or "agent")

    def _is_duplicate_agent_invocation(self, tool: AgentTool) -> bool:
        """Return True if an equivalent agent invocation tool is already registered."""
        tool_id = tool.tool_id
        tool_name = tool.name

        existing: RuntimeTool | None = None
        if tool_id:
            existing = self._resolve_tool(tool_id)
        if existing is None and tool_name:
            existing = self._resolve_tool(tool_name)

        if existing is None:
            return False

        if existing is tool:
            return True

        if isinstance(existing, AgentTool):
            return existing.target_agent_id == tool.target_agent_id
        if isinstance(existing, _AgentInvocationTool) and existing.tool_type == "agent":
            return existing.target_agent_id == tool.target_agent_id
        return False

    # MARK: - Private Helpers

    def _get_dynamic_tools(self) -> list[FunctionTool]:
        dynamic_tools = getattr(self._state_compiler, "_dynamic_tools", [])
        return cast(list[FunctionTool], dynamic_tools)

    def _resolve_tool(self, tool_id: str) -> RuntimeTool | None:
        try:
            return self._tool_execution.resolve_tool(tool_id)
        except ValueError:
            return None
        except AttributeError:
            index = getattr(self._tool_execution, "_tool_index", {})
            if isinstance(index, dict):
                return cast(dict[str, RuntimeTool], index).get(tool_id)
            return None

    def _register_tool(self, tool: RuntimeTool) -> None:
        register_tool = getattr(self._tool_execution, "_register_tool", None)
        if not callable(register_tool):
            raise AttributeError("Tool execution service does not support tool registration")
        cast(Callable[[RuntimeTool], None], register_tool)(tool)

    def _map_tools_to_agent(self, tools: Sequence[BaseTool], agent_name: str) -> None:
        for tool in tools:
            self._register_tool_mapping(tool, agent_name)

    def _register_tool_mapping(self, tool: BaseTool, agent_name: str) -> None:
        tool_id = tool.tool_id
        tool_name = tool.name
        if tool_id:
            self._tool_to_agent_map[tool_id] = agent_name
        if tool_name:
            self._tool_to_agent_map[tool_name] = agent_name
        logger.debug(
            "[TOOLING] Registered tool: id=%r, name=%r -> agent=%r",
            tool_id,
            tool_name,
            agent_name,
        )


__all__ = ["ToolIndexCoordinator"]
