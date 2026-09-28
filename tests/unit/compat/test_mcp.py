"""Executable v2 MCP compatibility tests."""

from __future__ import annotations

import sys
from textwrap import dedent
from typing import TYPE_CHECKING

from maivn import MCPServer

if TYPE_CHECKING:
    from pathlib import Path


def test_stdio_mcp_server_discovers_and_calls_tools(tmp_path: Path) -> None:
    """The public MCP adapter executes a local stdio server without v1 imports."""
    server_script = tmp_path / 'server.py'
    server_script.write_text(
        dedent(
            """
            import json
            import sys

            for line in sys.stdin:
                request = json.loads(line)
                method = request.get('method')
                if 'id' not in request:
                    continue
                if method == 'initialize':
                    result = {
                        'protocolVersion': '2024-11-05',
                        'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'test', 'version': '1'},
                    }
                elif method == 'tools/list':
                    result = {
                        'tools': [{
                            'name': 'add',
                            'description': 'Add two integers.',
                            'inputSchema': {
                                'type': 'object',
                                'properties': {
                                    'a': {'type': 'integer'},
                                    'b': {'type': 'integer'},
                                },
                                'required': ['a', 'b'],
                            },
                        }]
                    }
                elif method == 'tools/call':
                    arguments = request['params']['arguments']
                    result = {
                        'structuredContent': {'sum': arguments['a'] + arguments['b']},
                        'isError': False,
                    }
                else:
                    result = {}
                print(json.dumps({
                    'jsonrpc': '2.0',
                    'id': request['id'],
                    'result': result,
                }), flush=True)
            """
        ),
        encoding='utf-8',
    )
    server = MCPServer(
        name='calculator',
        transport='stdio',
        command=sys.executable,
        args=[str(server_script)],
        stdio_response_timeout_seconds=5,
    )

    try:
        tools = server.list_tools()
        result = server.call_tool('add', {'a': 3, 'b': 5})
    finally:
        server.close()

    assert [tool['name'] for tool in tools] == ['add']
    assert result == {'structuredContent': {'sum': 8}, 'isError': False}
