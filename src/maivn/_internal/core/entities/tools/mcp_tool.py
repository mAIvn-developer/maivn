"""MCP tool entity for tools sourced from MCP servers."""

# pyright: strict
from __future__ import annotations

from maivn_shared import ToolType
from pydantic import Field, JsonValue
from typing_extensions import override

from .base_tool import MCP_TOOL_TYPE, BaseTool

# MARK: - MCP Tool


class McpTool(BaseTool):
    """Tool wrapper for MCP server-provided tools."""

    tool_type: ToolType = Field(
        default=MCP_TOOL_TYPE,
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
    args_schema: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="JSON schema describing MCP tool input",
    )
    default_args: dict[str, JsonValue] | None = Field(
        default=None,
        description="Default arguments applied when the MCP tool is executed",
    )
    output_schema: dict[str, JsonValue] | None = Field(
        default=None,
        description="Optional JSON schema for MCP tool output",
    )
    annotations: dict[str, JsonValue] | None = Field(
        default=None,
        description="Optional MCP tool annotations",
    )
    server: object | None = Field(
        default=None,
        description="Optional MCP server reference for execution",
    )

    # MARK: - String Representation

    @override
    def __str__(self) -> str:
        return self._format_tool_label(f"mcp:{self.server_name}:{self.mcp_tool_name}")


# MARK: - Exports

__all__ = ["McpTool"]
