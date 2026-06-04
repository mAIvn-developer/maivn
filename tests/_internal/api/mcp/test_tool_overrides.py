# pyright: strict
"""Tests for MCP tool registration overrides."""

from __future__ import annotations

import pytest
from maivn_shared import DataDependency

from maivn import MCPServer, ToolOverride
from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.mcp.tools import MCPToolDefinition
from maivn._internal.core.entities.tools import McpTool
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration


def _make_agent() -> Agent:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    client = Client.from_configuration(api_key="key", configuration=config)
    return Agent(name="t", client=client)


def _pin_tool_defs(
    server: MCPServer,
    tool_defs: list[MCPToolDefinition],
) -> None:
    """Replace ``server.list_tools`` with a static, no-transport stub.

    Uses ``object.__setattr__`` to bypass Pydantic's ``validate_assignment``,
    which would reject method overrides as unknown fields.
    """

    def _static() -> list[MCPToolDefinition]:
        return tool_defs

    object.__setattr__(server, "list_tools", _static)


def test_mcp_tool_override_matches_toolset_override_shape() -> None:
    server = MCPServer(
        name="remote",
        transport="http",
        url="https://mcp.example.test",
        default_tool_args={"limit": 5},
        tool_defaults={"search": {"limit": 10}},
        tool_overrides={
            "search": ToolOverride(
                name="inbox_search",
                description="Search inbox messages for triage.",
                tags=["email", "read"],
                metadata={"audit_zone": "gmail"},
                default_args={"query": "in:inbox"},
                always_execute=True,
                final_tool=True,
                dependencies=[DataDependency(arg_name="id", data_key="user_id")],
            )
        },
    )
    _pin_tool_defs(
        server,
        [
            MCPToolDefinition(
                name="search",
                description="Generic remote search.",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
        ],
    )
    agent = _make_agent()

    agent.register_mcp_servers(server)

    tool = agent.list_tools()[0]
    assert isinstance(tool, McpTool)
    assert tool.name == "inbox_search"
    assert tool.description == "Search inbox messages for triage."
    assert tool.tags == ["mcp", "mcp:remote", "email", "read"]
    assert tool.metadata == {"audit_zone": "gmail"}
    assert tool.default_args == {"limit": 10, "query": "in:inbox"}
    assert tool.always_execute is True
    assert tool.final_tool is True
    assert len(tool.dependencies) == 1


def test_mcp_tool_override_unknown_key_raises() -> None:
    server = MCPServer(
        name="remote",
        transport="http",
        url="https://mcp.example.test",
        tool_overrides={"misspelled": ToolOverride(description="No-op?")},
    )
    _pin_tool_defs(server, [MCPToolDefinition(name="search")])
    agent = _make_agent()

    with pytest.raises(ValueError, match="override keys"):
        agent.register_mcp_servers(server)
