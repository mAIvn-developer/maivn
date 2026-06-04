"""Agent invocation tool entity.

This tool wraps dynamic invocation of another agent inside a swarm.
"""

# pyright: strict
from __future__ import annotations

from maivn_shared import ToolType
from pydantic import Field

from .base_tool import AGENT_TOOL_TYPE
from .function_tool import FunctionTool

# MARK: - AgentTool


class AgentTool(FunctionTool):
    """Function-based tool that invokes another agent."""

    # MARK: - Fields

    tool_type: ToolType = Field(
        default=AGENT_TOOL_TYPE,
        description="Type of tool (always agent for this class)",
    )
    target_agent_id: str = Field(
        ...,
        description="Identifier of the agent this tool will invoke",
    )


# MARK: - Exports

__all__ = [
    "AgentTool",
]
