# MCP Integration

The maivn SDK supports [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) servers as external tool providers. This enables integration with a wide ecosystem of MCP-compatible tools.

## Execution Security

**MCP tools execute locally in your environment.** Whether using stdio or HTTP transport, MCP servers run in your environment - code is never transferred to or executed on maivn servers.

- **stdio transport**: Launches a local process on your machine
- **http transport**: Connects to an HTTP endpoint you control

The maivn server only receives tool schemas and orchestrates which tools to call. All actual tool execution happens locally.

## Import

```python
from maivn import MCPAutoSetup, MCPServer, MCPSoftErrorHandling
```

## MCPServer

Configuration model for MCP server connections.

```python
MCPServer(
    name: str,
    transport: Literal['http', 'stdio'] = 'stdio',
    url: str | None = None,
    command: str | None = None,
    args: list[str] = [],
    env: dict[str, str] | None = None,
    inherit_env: bool = True,
    inherit_env_allowlist: list[str] | None = None,
    working_dir: str | None = None,
    headers: dict[str, str] | None = None,
    protocol_version: str = '2025-06-18',
    tool_name_prefix: str | None = None,
    tool_name_separator: str = '__',
    default_tool_args: dict[str, Any] | None = None,
    tool_defaults: dict[str, dict[str, Any]] | None = None,
    max_calls_per_minute: int | None = None,
    max_calls_per_day: int | None = None,
    request_timeout_seconds: float | None = None,
    stdio_response_timeout_seconds: float | None = None,
    raise_on_tool_error: bool = False,
    auto_setup: MCPAutoSetup | None = None,
    soft_error_handling: MCPSoftErrorHandling | None = None,
)
```

### Parameters

| Parameter                 | Type                           | Default        | Description                                                   |
| ------------------------- | ------------------------------ | -------------- | ------------------------------------------------------------- |
| `name`                    | `str`                          | Required       | Logical name for the MCP server                               |
| `transport`               | `Literal['http', 'stdio']`     | `'stdio'`      | Communication protocol                                        |
| `url`                     | `str \| None`                  | `None`         | HTTP endpoint (required for http transport)                   |
| `command`                 | `str \| None`                  | `None`         | Command to launch stdio server                                |
| `args`                    | `list[str]`                    | `[]`           | Arguments for stdio server                                    |
| `env`                     | `dict[str, str] \| None`       | `None`         | Environment variables                                         |
| `inherit_env`             | `bool`                         | `True`         | Whether stdio servers inherit the parent process environment  |
| `inherit_env_allowlist`   | `list[str] \| None`            | `None`         | Optional parent env vars to inherit when tightening stdio env |
| `working_dir`             | `str \| None`                  | `None`         | Working directory for stdio server                            |
| `headers`                 | `dict[str, str] \| None`       | `None`         | HTTP headers                                                  |
| `protocol_version`        | `str`                          | `'2025-06-18'` | MCP protocol version                                          |
| `tool_name_prefix`        | `str \| None`                  | Server name    | Prefix for tool names                                         |
| `tool_name_separator`     | `str`                          | `'__'`         | Separator between prefix and name                             |
| `default_tool_args`       | `dict \| None`                 | `None`         | Default args for all tools                                    |
| `tool_defaults`           | `dict[str, dict] \| None`      | `None`         | Per-tool default arguments                                    |
| `max_calls_per_minute`    | `int \| None`                  | `None`         | Rate limit (calls/minute)                                     |
| `max_calls_per_day`       | `int \| None`                  | `None`         | Rate limit (calls/day)                                        |
| `request_timeout_seconds` | `float \| None`                | `None`         | HTTP timeout override                                         |
| `stdio_response_timeout_seconds` | `float \| None`         | `None`         | Timeout for stdio responses (None = wait indefinitely)        |
| `raise_on_tool_error`     | `bool`                         | `False`        | Raise on MCP tool errors                                      |
| `auto_setup`              | `MCPAutoSetup \| None`         | `None`         | Auto-setup configuration                                      |
| `soft_error_handling`     | `MCPSoftErrorHandling \| None` | `None`         | Detect soft errors in JSON payloads and optionally wait/retry |

## Transport Types

### stdio Transport

Launches a local process and communicates via stdin/stdout.

```python
mcp_server = MCPServer(
    name='local_tools',
    transport='stdio',
    command='python',
    args=['-m', 'my_mcp_server'],
    env={'API_KEY': 'secret'},
    stdio_response_timeout_seconds=30,  # Optional: avoid hanging forever
)
```

For hardened deployments, keep stdio inheritance tight while still allowing the runtime to launch:

```python
mcp_server = MCPServer(
    name='local_tools',
    transport='stdio',
    command='python',
    args=['-m', 'my_mcp_server'],
    inherit_env=False,
    inherit_env_allowlist=['OPENAI_API_KEY'],
    env={'API_KEY': 'explicit-token'},
    stdio_response_timeout_seconds=30,
)
```

When `inherit_env=False`, maivn still carries a small runtime baseline such as `PATH` so the subprocess can start. Add provider credentials through `env` or `inherit_env_allowlist` explicitly when you want tighter control.

### http Transport

Connects to an HTTP endpoint.

```python
mcp_server = MCPServer(
    name='remote_tools',
    transport='http',
    url='https://mcp.example.com/api',
    headers={'Authorization': 'Bearer token'},
)
```

## MCPAutoSetup

Auto-setup configuration for uvx-based MCP servers.

```python
MCPAutoSetup(
    provider: Literal['uvx'] = 'uvx',
    package: str,
    args: list[str] = [],
    env: dict[str, str] | None = None,
    working_dir: str | None = None,
    uvx_command: str | None = None,
)
```

### Parameters

| Parameter     | Type                     | Default  | Description                     |
| ------------- | ------------------------ | -------- | ------------------------------- |
| `provider`    | `Literal['uvx']`         | `'uvx'`  | Installer (currently only uvx)  |
| `package`     | `str`                    | Required | Package name to install/run     |
| `args`        | `list[str]`              | `[]`     | Arguments to pass to the server |
| `env`         | `dict[str, str] \| None` | `None`   | Environment variables           |
| `working_dir` | `str \| None`            | `None`   | Working directory               |
| `uvx_command` | `str \| None`            | `'uvx'`  | Override uvx binary name        |

### Example

```python
mcp_server = MCPServer(
    name='filesystem',
    transport='stdio',
    auto_setup=MCPAutoSetup(
        package='mcp-server-filesystem',
        args=['--root', '/data'],
    ),
)
```

This automatically runs: `uvx mcp-server-filesystem --root /data`

## Registering MCP Servers

Register MCP servers with an Agent or Swarm:

```python
from maivn import Agent, MCPServer

agent = Agent(name='mcp_agent', api_key='...')

# Single server
agent.register_mcp_servers(MCPServer(
    name='tools',
    transport='stdio',
    command='my-mcp-server',
))

# Multiple servers
agent.register_mcp_servers([
    MCPServer(name='fs', transport='stdio', command='fs-server'),
    MCPServer(name='api', transport='http', url='https://api.example.com'),
])
```

## Tool Name Mapping

MCP tools are registered with prefixed names to avoid conflicts:

```python
# MCP server exposes: read_file, write_file
# With default settings (prefix=server name, separator='__'):
# Tools become: tools__read_file, tools__write_file

mcp_server = MCPServer(
    name='tools',
    tool_name_prefix='fs',  # Custom prefix
    tool_name_separator='_',  # Custom separator
)
# Tools become: fs_read_file, fs_write_file
```

### Disable Prefix

```python
mcp_server = MCPServer(
    name='tools',
    tool_name_prefix='',  # Empty string = no prefix
)
# Tools keep original names: read_file, write_file
```

## Default Arguments

### Global Defaults

Apply default arguments to all MCP tools:

```python
mcp_server = MCPServer(
    name='api',
    transport='http',
    url='https://api.example.com',
    default_tool_args={
        'timeout': 30,
        'format': 'json',
    },
)
```

### Per-Tool Defaults

Apply defaults to specific tools:

```python
mcp_server = MCPServer(
    name='fs',
    transport='stdio',
    command='fs-server',
    tool_defaults={
        'read_file': {'encoding': 'utf-8'},
        'write_file': {'create_dirs': True},
    },
)
```

Per-tool defaults override global defaults.

## Rate Limiting

Protect external APIs with rate limits:

```python
mcp_server = MCPServer(
    name='external_api',
    transport='http',
    url='https://api.example.com',
    max_calls_per_minute=60,  # 1 call/second average
    max_calls_per_day=1000,    # Daily limit
)
```

Rate limiting uses a sliding window algorithm. Calls exceeding the limit will block until a slot is available.

To reduce bursts and improve performance, maivn also paces calls locally based on the configured limits (tracking the next allowed call time in memory).

## Soft Error Handling

Some providers return HTTP 200 but include errors in the response body (e.g., rate limits, quotas, plan restrictions).
When enabled, maivn can detect these responses and automatically wait/retry.

```python
from maivn import MCPServer, MCPSoftErrorHandling

mcp_server = MCPServer(
    name='external_api',
    transport='http',
    url='https://mcp.example.com/api',
    max_calls_per_minute=5,
    soft_error_handling=MCPSoftErrorHandling(
        enabled=True,
        max_retries=2,
        initial_backoff_seconds=10,
        max_backoff_seconds=60,
    ),
)
```

When a soft error is detected:

- maivn returns the MCP tool result with `is_error=True`
- the result includes a `soft_error` payload with the detected message
- if retries are configured, maivn waits and retries before returning

## Error Handling

By default, MCP tool errors are returned as results. Enable raising:

```python
mcp_server = MCPServer(
    name='tools',
    transport='stdio',
    command='my-server',
    raise_on_tool_error=True,  # Raises ValueError on tool errors
)
```

## Lifecycle

### Listing Servers

```python
servers = agent.list_mcp_servers()
for server in servers:
    print(f'{server.name}: {server.transport}')
```

### Closing Servers

MCP servers are automatically closed when the agent is closed:

```python
agent.close()  # Closes all MCP servers
```

Or close explicitly:

```python
agent.close_mcp_servers()
```

## Complete Example

```python
from maivn import Agent, MCPServer, MCPAutoSetup
from maivn.messages import HumanMessage

# Create agent
agent = Agent(
    name='file_agent',
    description='Agent with file system access',
    system_prompt='You can read and write files.',
    api_key='your-api-key',
)

# Register filesystem MCP server
agent.register_mcp_servers(MCPServer(
    name='fs',
    transport='stdio',
    auto_setup=MCPAutoSetup(
        package='mcp-server-filesystem',
        args=['--root', '/home/user/data'],
    ),
    tool_name_prefix='file',
    max_calls_per_minute=100,
))

# Use the agent (MCP tools are available)
response = agent.invoke([
    HumanMessage(content='List files in the current directory')
])

# Clean up
agent.close()
```

## See Also

- [Agent](agent.md) - `register_mcp_servers()` method
- [Tools Guide](../guides/tools.md) - Tool patterns
- [MCP Specification](https://modelcontextprotocol.io/) - Protocol details
