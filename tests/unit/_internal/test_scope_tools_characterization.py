"""Characterization tests for scope tool targeting, MCP compilation, and name validation."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, MCPServer, depends_on_agent

JsonObject = dict[str, Any]


def _final_sse() -> httpx.Response:
    event: JsonObject = {
        'event_id': 'evt-final',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': 'ses-tools',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-final',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-07-04T12:00:00Z',
            },
            'usage': {},
            'tool_calls': {'count': 0, 'names': []},
        },
        'ts': '2026-07-04T12:00:00Z',
    }
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: 1\nevent: final\ndata: {json.dumps(event)}\n\n',
    )


def _agent(bodies: list[JsonObject]) -> Agent:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            bodies.append(cast('JsonObject', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-tools', 'stream_position': 0}
            )
        return _final_sse()

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    return Agent(name='toolbox', client=client)


def _tool_names(body: JsonObject) -> list[str]:
    return [tool['name'] for tool in body['tools']]


def test_targeted_tools_accept_legacy_qualified_names_once() -> None:
    """Qualified agent.tool names resolve by their final segment and are not duplicated."""
    bodies: list[JsonObject] = []
    agent = _agent(bodies)
    _ = agent.add_tool(lambda: 'a', name='alpha')
    _ = agent.add_tool(lambda: 'b', name='beta')

    _ = agent.invoke('go', targeted_tools=[' beta ', 'studio.beta', 'beta'])

    assert _tool_names(bodies[0]) == ['beta']


@pytest.mark.parametrize(
    ('targeted', 'expected'),
    [
        (
            ['missing', 'x.gone'],
            "Unknown targeted tool(s): 'missing', 'x.gone'. Available tools: alpha",
        ),
        ([], 'targeted_tools must contain at least one tool name'),
    ],
)
def test_targeted_tools_reject_unknown_or_empty_selections(
    targeted: list[str], expected: str
) -> None:
    """Unknown names list the available tools; an empty selection is refused."""
    bodies: list[JsonObject] = []
    agent = _agent(bodies)
    _ = agent.add_tool(lambda: 'a', name='alpha')

    with pytest.raises(ValueError) as raised:  # noqa: PT011 - exact message asserted below.
        _ = agent.invoke('go', targeted_tools=targeted)

    assert str(raised.value) == expected
    assert bodies == []


def test_targeted_tools_without_any_tools_report_none_available() -> None:
    """An agent without tools names '(none)' as the available set."""
    with pytest.raises(
        ValueError, match=r"^Unknown targeted tool\(s\): 'x'\. Available tools: \(none\)$"
    ):
        _ = _agent([]).invoke('go', targeted_tools=['x'])


def test_targeted_tool_with_unregistered_producer_is_refused() -> None:
    """A targeted consumer whose producer is not registered cannot be selected."""
    agent = _agent([])
    other = _agent([])

    @other.toolify(name='producer')
    def producer() -> str:
        return 'value'

    @agent.toolify(name='consumer').depends_on_tool(producer, 'value')
    def consumer(value: str) -> str:
        return value

    _ = consumer
    with pytest.raises(ValueError) as raised:  # noqa: PT011 - exact message asserted below.
        _ = agent.invoke('go', targeted_tools=['consumer'])

    assert str(raised.value) == "Targeted tool 'consumer' depends on unavailable tool 'producer'"


def test_targeted_tool_ignores_agent_dependencies_and_shared_producers() -> None:
    """Agent edges are not tool producers, and a shared producer is shipped once first."""
    bodies: list[JsonObject] = []
    agent = _agent(bodies)
    delegate = _agent([])

    @agent.toolify(name='base')
    def base() -> str:
        return 'base'

    @agent.toolify(name='left').depends_on_tool(base, 'value')
    def left(value: str) -> str:
        return value

    @agent.toolify(name='right').depends_on_tool(base, 'value')
    @depends_on_agent(delegate, 'notes')
    def right(value: str, notes: str) -> str:
        return value + notes

    _ = (left, right)
    _ = agent.invoke('go', targeted_tools=['left', 'right'])

    assert _tool_names(bodies[0]) == ['base', 'left', 'right']


class _InventoryServer(MCPServer):
    """MCP server double that lists a fixed tool catalog without a subprocess."""

    def list_tools(self) -> list[dict[str, Any]]:
        """Return one tool without a schema or description."""
        return [{'name': 'count'}]


class _NamelessServer(MCPServer):
    """MCP server double that returns a malformed catalog entry."""

    def list_tools(self) -> list[dict[str, Any]]:
        """Return one tool without a name."""
        return [{'description': 'no name'}]


def test_mcp_tools_get_default_schema_and_description() -> None:
    """A catalog entry without schema or description gets an empty object schema."""
    agent = _agent([])
    agent.register_mcp_servers(_InventoryServer(name='inventory'))
    agent.mcp_servers.append(cast('MCPServer', object()))

    (tool,) = agent.compile_tools()

    assert tool.name == 'inventory__count'
    assert tool.description == 'Execute MCP tool count.'
    assert tool.input_schema == {'type': 'object', 'properties': {}}
    assert tool.metadata == {'mcp_server': 'inventory', 'mcp_tool': 'count'}


def test_mcp_tool_without_name_is_rejected() -> None:
    """A catalog entry without a name is a configuration error naming the server."""
    agent = _agent([])
    agent.register_mcp_servers(_NamelessServer(name='broken'))

    with pytest.raises(ValueError, match=r"^MCP server 'broken' returned a tool without a name$"):
        _ = agent.compile_tools()


def test_duplicate_tool_names_fail_validation_but_raw_entries_are_skipped() -> None:
    """Two compiled tools with one name are refused; uncompiled entries are ignored."""
    first = _agent([])
    _ = first.add_tool(lambda: 1, name='same')
    second = _agent([])
    _ = second.add_tool(lambda: 2, name='same')
    agent = _agent([])
    agent.tools.extend([*first.compile_tools(), *second.compile_tools()])
    clean = _agent([])
    clean.tools.append(lambda: 3)

    with pytest.raises(ValueError, match=r"^Agent 'toolbox' has duplicate tool name: same$"):
        agent.validate_tool_configuration()
    clean.validate_tool_configuration()
