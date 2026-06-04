"""MCP tool execution helper."""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import ExecutionContext, McpTool
from maivn._internal.core.exceptions import ToolExecutionError

# MARK: Protocols


class McpServerProtocol(Protocol):
    """MCP server surface required by the executor."""

    def call_tool(self, tool_name: str, args: dict[str, object]) -> object:
        """Call a tool on the MCP server."""
        ...


# MARK: MCP Executor


class McpExecutor:
    """Executes MCP tools by calling registered MCP servers."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        self._logger: LoggerProtocol | None = logger

    def execute(
        self,
        tool: McpTool,
        args: dict[str, object],
        context: ExecutionContext | None,
    ) -> object:
        server = cast(McpServerProtocol | None, tool.server)
        if server is None and context is not None:
            server = _mcp_servers_from_scope(context.scope).get(tool.server_name)

        if server is None:
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP server '{tool.server_name}' not found",
            )

        if self._logger:
            self._logger.info("[MCP] Executing %s via %s", tool.mcp_tool_name, tool.server_name)

        try:
            return server.call_tool(tool.mcp_tool_name, args)
        except Exception as exc:  # noqa: BLE001 - MCP server failures are reported as tool failure
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP tool execution failed: {exc}",
                original_error=exc,
            ) from exc


# MARK: Helpers


def _mcp_servers_from_scope(scope: object | None) -> Mapping[str, McpServerProtocol]:
    servers = cast(object, getattr(scope, "_mcp_servers", {}))
    if isinstance(servers, Mapping):
        return cast(Mapping[str, McpServerProtocol], servers)
    return {}


__all__ = ["McpExecutor"]
