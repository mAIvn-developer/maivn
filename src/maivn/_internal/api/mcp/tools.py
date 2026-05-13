"""MCP tool definitions and utilities."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# MARK: Constants

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_CLIENT_NAME = "maivn"
DEFAULT_CLIENT_TITLE = "Maivn SDK"
DEFAULT_CLIENT_VERSION = "unknown"


# MARK: Utility Functions


def sanitize_identifier(value: str) -> str:
    """Sanitize a string to be a valid Python identifier.

    Args:
        value: The string to sanitize.

    Returns:
        A sanitized identifier string.
    """
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_")
    return safe or "mcp_tool"


# MARK: MCPToolDefinition


class MCPToolDefinition(BaseModel):
    """Definition for a tool exposed by an MCP server.

    This model represents the schema of a tool as reported by an MCP server,
    including its name, description, and input/output schemas.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="inputSchema")
    output_schema: dict[str, Any] | None = Field(default=None, alias="outputSchema")
    annotations: dict[str, Any] | None = None


__all__ = [
    "DEFAULT_CLIENT_NAME",
    "DEFAULT_CLIENT_TITLE",
    "DEFAULT_CLIENT_VERSION",
    "DEFAULT_PROTOCOL_VERSION",
    "MCPToolDefinition",
    "sanitize_identifier",
]
