"""MCP tool execution helper."""

from __future__ import annotations

from typing import Any

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import ExecutionContext, McpTool
from maivn._internal.core.exceptions import ToolExecutionError


class McpExecutor:
    """Executes MCP tools by calling registered MCP servers."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        self._logger = logger

    def execute(self, tool: McpTool, args: dict[str, Any], context: ExecutionContext | None) -> Any:
        server = getattr(tool, "server", None)
        if server is None and context is not None:
            scope = getattr(context, "scope", None)
            server = getattr(scope, "_mcp_servers", {}).get(tool.server_name) if scope else None

        if server is None:
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP server '{tool.server_name}' not found",
            )

        if self._logger:
            self._logger.info("[MCP] Executing %s via %s", tool.mcp_tool_name, tool.server_name)

        try:
            return server.call_tool(tool.mcp_tool_name, args)
        except Exception as exc:  # noqa: BLE001
            raise ToolExecutionError(
                tool_id=tool.name,
                reason=f"MCP tool execution failed: {exc}",
                original_error=exc,
            ) from exc


__all__ = ["McpExecutor"]
