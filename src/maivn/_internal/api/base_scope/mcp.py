"""MCP registry helpers for BaseScope."""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard, cast

from maivn_shared import create_uuid
from pydantic import JsonValue

from maivn._internal.api.mcp import MCPServer
from maivn._internal.api.mcp.tools import MCPToolDefinition
from maivn._internal.api.tool_override import apply_override
from maivn._internal.core.entities.tools import McpTool
from maivn._internal.core.registrars import ToolRegistrar

# MARK: Types

JsonObject = dict[str, JsonValue]

# MARK: - MCP Registry


class McpRegistry:
    """Register and manage MCP servers/tools for a scope."""

    # MARK: - Initialization

    def __init__(self, scope: object) -> None:
        self._scope: object = scope

    # MARK: - Public API

    def register_servers(self, servers: MCPServer | Sequence[MCPServer]) -> None:
        for server in self._normalize_mcp_servers(servers):
            self._register_mcp_server(server)
        setattr(self._scope, "_tools_dirty", True)  # noqa: B010 - Pydantic PrivateAttr.

    def list_servers(self) -> list[MCPServer]:
        return list(self._mcp_servers().values())

    def close_servers(self) -> None:
        for server in self._mcp_servers().values():
            try:
                server.close()
            except Exception:  # noqa: BLE001 - MCP close is best-effort cleanup.
                pass

    # MARK: - Private Helpers

    def _normalize_mcp_servers(self, servers: MCPServer | Sequence[MCPServer]) -> list[MCPServer]:
        if isinstance(servers, MCPServer):
            return [servers]
        if _is_mcp_server_sequence(servers):
            return list(servers)
        raise TypeError("register_mcp_servers expects an MCPServer or sequence of MCPServer")

    def _register_mcp_server(self, server: MCPServer) -> None:
        mcp_servers = self._mcp_servers()
        existing = mcp_servers.get(server.name)
        if existing is not None and existing is not server:
            raise ValueError(f"MCP server name already registered: {server.name}")

        tool_defs = server.list_tools()
        self._validate_mcp_tool_overrides(server, tool_defs)

        mcp_servers[server.name] = server
        for tool_def in tool_defs:
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

        default_args = _json_object(server.resolve_tool_defaults(tool_def.name))
        override = server.resolve_tool_override(tool_def.name)
        applied = apply_override(
            override,
            base_name=tool_name,
            base_description=resolved_description,
            base_tags=["mcp", f"mcp:{server.name}"],
            base_metadata=None,
            base_always_execute=False,
            base_final_tool=False,
            base_default_args=default_args,
        )

        tool = McpTool(
            name=applied.name or tool_name,
            description=applied.description or "",
            tags=applied.tags,
            server_name=server.name,
            mcp_tool_name=tool_def.name,
            args_schema=_json_object(tool_def.input_schema or {}),
            default_args=_json_object(applied.default_args) if applied.default_args else None,
            output_schema=(
                _json_object(tool_def.output_schema) if tool_def.output_schema is not None else None
            ),
            annotations=(
                _json_object(tool_def.annotations) if tool_def.annotations is not None else None
            ),
            server=server,
            tool_id=create_uuid(f"mcp:{server.name}:{tool_def.name}"),
            always_execute=applied.always_execute,
            final_tool=applied.final_tool,
            metadata=applied.metadata,
            before_execute=applied.before_execute,
            after_execute=applied.after_execute,
        )
        for dep in applied.dependencies:
            tool.add_dependency(dep)
        self._tool_registrar()(tool)

    @staticmethod
    def _validate_mcp_tool_overrides(
        server: MCPServer,
        tool_defs: list[MCPToolDefinition],
    ) -> None:
        overrides = getattr(server, "tool_overrides", None)
        if not isinstance(overrides, dict) or not overrides:
            return
        override_map = cast(Mapping[str, object], overrides)
        discovered_names = {tool_def.name for tool_def in tool_defs}
        unknown = sorted(set(override_map) - discovered_names)
        if unknown:
            raise ValueError(
                f"MCP server {server.name!r} has no tools matching override keys: "
                + f"{unknown}. Override keys must match raw MCP tool names before "
                + "maivn prefixes are applied."
            )

    def _mcp_servers(self) -> dict[str, MCPServer]:
        return cast(
            dict[str, MCPServer],
            getattr(self._scope, "_mcp_servers"),  # noqa: B009 - Pydantic PrivateAttr.
        )

    def _tool_registrar(self) -> ToolRegistrar:
        return cast(
            ToolRegistrar,
            getattr(self._scope, "_tool_registrar"),  # noqa: B009 - Pydantic PrivateAttr.
        )


def _json_object(value: object) -> JsonObject:
    return dict(cast(Mapping[str, JsonValue], value))


def _is_mcp_server_sequence(value: object) -> TypeGuard[Sequence[MCPServer]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


__all__ = ["McpRegistry"]
