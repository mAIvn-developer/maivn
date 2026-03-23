"""MCP registry helpers for BaseScope."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from maivn_shared import create_uuid

from maivn._internal.api.mcp import MCPServer
from maivn._internal.api.mcp.tools import MCPToolDefinition
from maivn._internal.core.entities.tools import McpTool

# MARK: - MCP Registry


class McpRegistry:
    """Register and manage MCP servers/tools for a scope."""

    # MARK: - Initialization

    def __init__(self, scope: Any) -> None:
        self._scope = scope

    # MARK: - Public API

    def register_servers(self, servers: MCPServer | Sequence[MCPServer]) -> None:
        for server in self._normalize_mcp_servers(servers):
            self._register_mcp_server(server)
        self._scope._tools_dirty = True

    def list_servers(self) -> list[MCPServer]:
        return list(self._scope._mcp_servers.values())

    def close_servers(self) -> None:
        for server in self._scope._mcp_servers.values():
            try:
                server.close()
            except Exception:
                pass

    # MARK: - Private Helpers

    def _normalize_mcp_servers(self, servers: MCPServer | Sequence[MCPServer]) -> list[MCPServer]:
        if isinstance(servers, MCPServer):
            return [servers]
        if isinstance(servers, Sequence) and not isinstance(servers, (str, bytes)):
            return list(servers)
        raise TypeError("register_mcp_servers expects an MCPServer or sequence of MCPServer")

    def _register_mcp_server(self, server: MCPServer) -> None:
        existing = self._scope._mcp_servers.get(server.name)
        if existing is not None and existing is not server:
            raise ValueError(f"MCP server name already registered: {server.name}")

        self._scope._mcp_servers[server.name] = server
        for tool_def in server.list_tools():
            self._register_mcp_tool(server, tool_def)

    def _register_mcp_tool(self, server: MCPServer, tool_def: MCPToolDefinition) -> None:
        tool_name = server.build_tool_name(tool_def.name)
        description = tool_def.description or ""
        title = tool_def.title or ""

        if title and description and title not in description:
            resolved_description = f"{title}. {description}"
        elif description:
            resolved_description = description
        elif title:
            resolved_description = title
        else:
            resolved_description = f"MCP tool '{tool_def.name}' from {server.name}."

        default_args = server.resolve_tool_defaults(tool_def.name)

        tool = McpTool(
            name=tool_name,
            description=resolved_description,
            tags=["mcp", f"mcp:{server.name}"],
            server_name=server.name,
            mcp_tool_name=tool_def.name,
            args_schema=tool_def.input_schema or {},
            default_args=default_args or None,
            output_schema=tool_def.output_schema,
            annotations=tool_def.annotations,
            server=server,
            tool_id=create_uuid(f"mcp:{server.name}:{tool_def.name}"),
        )
        self._scope._tool_registrar(tool)


__all__ = ["McpRegistry"]
