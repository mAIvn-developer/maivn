# MCP Integration

[MCP (Model Context Protocol)](https://modelcontextprotocol.io) lets agents
call tools that live in another process — local stdio servers, remote HTTP
servers, or third-party servers installed on demand.

Register an MCP server and its tools become first-class tools on the agent:
the agent picks when to call them, the runtime handles the wire protocol.

## Stdio server (local)

Run a local Python MCP server over stdio:

```python
import sys
from pathlib import Path
from maivn import Agent, MCPServer

stdio_mcp = MCPServer(
    name='local_stdio',
    transport='stdio',
    command=sys.executable,
    args=[str(Path('path/to/stdio_server.py'))],
)

agent = Agent(name='Stdio MCP Agent', system_prompt='...', api_key='...')
agent.register_mcp_servers([stdio_mcp])
```

The runtime spawns the server, runs MCP `initialize`/`tools/list`, and
exposes each remote tool to the agent as `local_stdio__<tool_name>` (the
server name is prepended to disambiguate when multiple servers are
attached).

## HTTP server (remote)

The HTTP transport is identical from the SDK's perspective — just point
the server at a URL:

```python
http_mcp = MCPServer(
    name='local_http',
    transport='http',
    url='http://127.0.0.1:8080/mcp',
)

agent.register_mcp_servers([http_mcp])
```

The agent calls `local_http__<tool_name>` exactly like a stdio server's
tools. Headers, auth, and rate limiting are configured on the
`MCPServer`.

## Multiple servers on one agent

Mix transports freely:

```python
agent.register_mcp_servers([
    MCPServer(name='local_http', transport='http', url='http://127.0.0.1:8080/mcp'),
    MCPServer(name='local_stdio', transport='stdio', command=sys.executable, args=['server.py']),
])

agent.invoke([HumanMessage(content=(
    "Call local_http__echo_http with text='hello', then local_stdio__add_numbers "
    "with a=3 and b=5. Return the MCPProtocolSummary tool."
))])
```

## Third-party stdio servers via `uvx`

For installable third-party servers (e.g. `mcp-server-fetch`), the
auto-setup helper drops them into an isolated environment without polluting
your global Python:

```python
from maivn import Agent, MCPAutoSetup, MCPServer, MCPSoftErrorHandling

fetch_server = MCPServer(
    name='fetch',
    transport='stdio',
    auto_setup=MCPAutoSetup(
        tool='uvx',
        package='mcp-server-fetch',
    ),
    soft_error_handling=MCPSoftErrorHandling(
        enabled=True,
        max_retries=2,
        initial_backoff_seconds=10,
        max_backoff_seconds=60,
    ),
)

agent = Agent(name='Fetch Agent', system_prompt='...', api_key='...')
agent.register_mcp_servers([fetch_server])
```

`MCPAutoSetup` ensures the package is available before the server starts.
`MCPSoftErrorHandling` controls retry behavior when the server is flaky —
useful when calling rate-limited upstream APIs.

## Rate limits and quotas

MCP servers can declare a per-minute call cap to protect upstream APIs:

```python
alpha_server = MCPServer(
    name='alpha_vantage',
    transport='stdio',
    command=sys.executable,
    args=['path/to/alpha_vantage_server.py'],
    env={'ALPHA_VANTAGE_API_KEY': os.environ['ALPHA_VANTAGE_API_KEY']},
    max_calls_per_minute=5,
)
```

When the cap trips, calls are queued (or rejected, depending on
configuration) — the agent sees the same return shape, so its behavior
degrades gracefully.

## Combining MCP with structured output

MCP tools work the same way as registered tools — including with
`final_tool` and `structured_output`:

```python
from pydantic import BaseModel, Field

class MCPProtocolSummary(BaseModel):
    """Capture MCP protocol results."""
    http_result: dict = Field(..., description='Result from HTTP MCP tool')
    stdio_result: dict = Field(..., description='Result from stdio MCP tool')
    notes: str = Field(default='Protocol demo complete.')

agent.toolify(name='mcp_protocol_summary', final_tool=True)(MCPProtocolSummary)

agent.structured_output(model=MCPProtocolSummary).invoke(messages)
```

## Closing connections

When the agent is done, close the MCP connections so subprocesses and
sockets shut down cleanly:

```python
try:
    agent.invoke(messages)
finally:
    agent.close()
```

In long-running services, attach this to the process lifecycle (e.g.
FastAPI's shutdown hook).

## What's next

- **[Agents & Tools](./agents-and-tools.md)** — combining MCP tools with
  registered tools, hooks, and cross-agent dependencies.
- **[Real-World Projects](./projects.md)** — the financial-planner project
  uses MCP servers for live market data.
- **MCP API reference**: [`docs/api/mcp.md`](../api/mcp.md).
