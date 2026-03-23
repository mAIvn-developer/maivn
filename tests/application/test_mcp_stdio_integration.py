from __future__ import annotations

import sys
import textwrap

from maivn import MCPServer


def test_mcp_stdio_server_list_and_call() -> None:
    script = textwrap.dedent(
        """
        import json
        import sys

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in payload:
                continue
            method = payload.get("method", "")
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18"}
            elif method == "tools/list":
                result = {"tools": [{"name": "hello", "description": "hi", "inputSchema": {}}]}
            elif method == "tools/call":
                result = {"content": "ok", "structuredContent": {"ok": True}}
            else:
                result = {}
            response = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
            sys.stdout.write(json.dumps(response) + "\\n")
            sys.stdout.flush()
        """
    ).strip()

    server = MCPServer(
        name="test",
        transport="stdio",
        command=sys.executable,
        args=["-u", "-c", script],
        stdio_response_timeout_seconds=2.0,
    )

    try:
        tools = server.list_tools()
        assert tools[0].name == "hello"
        result = server.call_tool("hello", {"x": 1})
        assert result["content"] == "ok"
        assert result["structured_content"]["ok"] is True
    finally:
        server.close()
