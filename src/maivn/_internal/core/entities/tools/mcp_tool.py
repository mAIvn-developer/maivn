"""MCP tool entity for tools sourced from MCP servers."""

from __future__ import annotations

from typing import Any

from maivn_shared import ToolType
from pydantic import Field

from .base_tool import BaseTool


class McpTool(BaseTool):
    """Tool wrapper for MCP server-provided tools."""

    tool_type: ToolType = Field(
        default="mcp",
        description="Type of tool (always mcp for this class)",
    )
    server_name: str = Field(
        ...,
        description="Registered MCP server name",
    )
    mcp_tool_name: str = Field(
        ...,
        description="Original MCP tool name to invoke",
    )
    args_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON schema describing MCP tool input",
    )
    default_args: dict[str, Any] | None = Field(
        default=None,
        description="Default arguments applied when the MCP tool is executed",
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="Optional JSON schema for MCP tool output",
    )
    annotations: dict[str, Any] | None = Field(
        default=None,
        description="Optional MCP tool annotations",
    )
    server: Any | None = Field(
        default=None,
        description="Optional MCP server reference for execution",
    )

    def is_executable(self) -> bool:
        return True

    def __str__(self) -> str:
        return f"{self.name} (mcp:{self.server_name}:{self.mcp_tool_name})"


__all__ = ["McpTool"]
