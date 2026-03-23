"""Agent invocation tool entity.

This tool wraps dynamic invocation of another agent inside a swarm.
"""

from __future__ import annotations

from maivn_shared import ToolType
from pydantic import Field

from .function_tool import FunctionTool

# MARK: - AgentTool


class AgentTool(FunctionTool):
    """Function-based tool that invokes another agent."""

    # MARK: - Fields

    tool_type: ToolType = Field(
        default="agent",
        description="Type of tool (always agent for this class)",
    )
    target_agent_id: str = Field(
        ...,
        description="Identifier of the agent this tool will invoke",
    )

    # MARK: - Properties

    @property
    def agent_id(self) -> str:
        """Expose target agent identifier for compatibility."""
        return self.target_agent_id


# MARK: - Exports

__all__ = [
    "AgentTool",
]
