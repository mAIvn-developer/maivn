"""Tool index and mapping helpers for AgentOrchestrator."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

# MARK: - Tool Index Coordinator


class ToolIndexCoordinator:
    """Track tool indexing and agent mapping for orchestration."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        tool_execution: Any,
        tool_exec_orchestrator: Any,
        state_compiler: Any,
        agent: Any,
    ) -> None:
        self._tool_execution = tool_execution
        self._tool_exec_orchestrator = tool_exec_orchestrator
        self._state_compiler = state_compiler
        self._agent = agent
        self._tool_to_agent_map: dict[str, str] = {}
        self._agent_count = 1

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
            fallback_name = getattr(self._agent, "name", None)
            if fallback_name:
                logger.debug(
                    f"[TOOLING] tool_agent_lookup({key!r}) -> None in map, "
                    f"falling back to current agent: {fallback_name!r}"
                )
                return fallback_name
            logger.debug(
                f"[TOOLING] tool_agent_lookup({key!r}) -> None. "
                f"Known keys: {list(self._tool_to_agent_map.keys())}"
            )
        return result

    # MARK: - Index Management

    def rebuild_tool_index(self, tools: Sequence[Any]) -> None:
        self._tool_execution.rebuild_index(list(tools))
        self._tool_exec_orchestrator.clear_results()

    def rebuild_tool_index_with_dynamic_tools(self, base_tools: list[Any]) -> None:
        dynamic_tools = getattr(self._state_compiler, "_dynamic_tools", [])
        if dynamic_tools:
            self.rebuild_tool_index(list(base_tools) + dynamic_tools)

    def build_tool_agent_mapping(self, swarm: Any | None) -> None:
        self._tool_to_agent_map.clear()
        if swarm:
            agents = getattr(swarm, "agents", [])
            self._agent_count = len(agents) if agents else 1
            for agent in agents:
                self._map_tools_to_agent(agent.list_tools(), getattr(agent, "name", "agent"))
            for tool in getattr(self._state_compiler, "_dynamic_tools", []):
                self._register_tool_mapping(tool, getattr(self._agent, "name", "agent"))
        else:
            self._agent_count = 1
            self._map_tools_to_agent(
                self._agent.list_tools(), getattr(self._agent, "name", "agent")
            )

    def register_swarm_agent_tools(self, agent_tools: list[Any]) -> None:
        for tool in agent_tools:
            if self._is_duplicate_agent_invocation(tool):
                agent_name = getattr(tool, "name", None)
                if agent_name:
                    self._register_tool_mapping(tool, agent_name)
                else:
                    self._register_tool_mapping(tool, getattr(self._agent, "name", "agent"))
                continue

            self._tool_execution._register_tool(tool)
            # Map invocation tools to the target agent name for accurate UI grouping.
            # AgentTool names are set to the target agent's name.
            agent_name = getattr(tool, "name", None)
            if agent_name:
                self._register_tool_mapping(tool, agent_name)
            else:
                self._register_tool_mapping(tool, getattr(self._agent, "name", "agent"))

    def _is_duplicate_agent_invocation(self, tool: Any) -> bool:
        """Return True if an equivalent agent invocation tool is already registered."""
        index = getattr(self._tool_execution, "_tool_index", {})
        if not isinstance(index, dict) or not index:
            return False

        tool_id = getattr(tool, "tool_id", None)
        tool_name = getattr(tool, "name", None)

        existing = None
        if tool_id and tool_id in index:
            existing = index.get(tool_id)
        elif tool_name and tool_name in index:
            existing = index.get(tool_name)

        if existing is None:
            return False

        if existing is tool:
            return True

        if getattr(existing, "tool_type", None) != "agent":
            return False

        return getattr(existing, "target_agent_id", None) == getattr(tool, "target_agent_id", None)

    # MARK: - Private Helpers

    def _map_tools_to_agent(self, tools: Any, agent_name: str) -> None:
        for tool in tools:
            self._register_tool_mapping(tool, agent_name)

    def _register_tool_mapping(self, tool: Any, agent_name: str) -> None:
        tool_id = getattr(tool, "tool_id", None)
        tool_name = getattr(tool, "name", None)
        if tool_id:
            self._tool_to_agent_map[tool_id] = agent_name
        if tool_name:
            self._tool_to_agent_map[tool_name] = agent_name
        logger.debug(
            f"[TOOLING] Registered tool: id={tool_id!r}, name={tool_name!r} -> agent={agent_name!r}"
        )


__all__ = ["ToolIndexCoordinator"]
