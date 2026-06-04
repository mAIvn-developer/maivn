# pyright: strict
"""MCP tool definitions and utilities."""

from __future__ import annotations

import re
from typing import ClassVar, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field

# MARK: Constants

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_CLIENT_NAME = "maivn"
DEFAULT_CLIENT_TITLE = "Maivn SDK"
DEFAULT_CLIENT_VERSION = "unknown"


# MARK: Types

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
JsonArray: TypeAlias = list[JsonValue]


# MARK: Utility Functions


def as_json_object(value: object) -> JsonObject | None:
    """Return ``value`` as a JSON object when it has the expected runtime shape."""
    if isinstance(value, dict):
        return cast(JsonObject, value)
    return None


def as_json_array(value: object) -> JsonArray | None:
    """Return ``value`` as a JSON array when it has the expected runtime shape."""
    if isinstance(value, list):
        return cast(JsonArray, value)
    return None


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

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, object] = Field(default_factory=dict, alias="inputSchema")
    output_schema: dict[str, object] | None = Field(default=None, alias="outputSchema")
    annotations: dict[str, object] | None = None


__all__ = [
    "DEFAULT_CLIENT_NAME",
    "DEFAULT_CLIENT_TITLE",
    "DEFAULT_CLIENT_VERSION",
    "DEFAULT_PROTOCOL_VERSION",
    "MCPToolDefinition",
    "sanitize_identifier",
]
