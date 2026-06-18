# Connecting MCP Servers

Your agent's tools do not have to be functions you wrote. With the
[Model Context Protocol](https://modelcontextprotocol.io/) (MCP), you can plug an
agent into tools that someone else built and runs — a filesystem server, a calendar
or email integration, an internal data service — and the agent uses them exactly like
the tools you define with `@agent.toolify()`.

This guide explains the mental model: what an MCP connection is, where the tools
actually run, how you choose a transport, and how you authenticate to a server you do
not own. For the exhaustive field reference see the
[MCP API Reference](../api/mcp.md); for copy-paste runnable setups see the
[MCP examples](../examples/mcp.md).

## What This Is and Why It Matters

MCP is an open standard for exposing tools to AI agents. An **MCP server** is a small
program that advertises a set of tools and knows how to run them. There is a growing
ecosystem of these servers — official ones, community ones, and ones you write
yourself — and they all speak the same protocol.

The point for you is reuse. Instead of writing a wrapper around the Google Calendar
API or a file-reading tool from scratch, you point your agent at an MCP server that
already does it. The agent then sees those tools alongside your own and decides when
to call them.

You describe a connection with the public `MCPServer` model and register it on an
`Agent` (or a `Swarm`) with `register_mcp_servers(...)`. From that point the server's
tools are part of the scope's tool surface, indistinguishable to the model from the
functions you wrote yourself.

```python
from maivn import Agent, MCPServer
from maivn.messages import HumanMessage

agent = Agent(name='assistant', api_key='...')

agent.register_mcp_servers(MCPServer(
    name='calendar',
    transport='http',
    url='https://mcp.example.com/api',
))

response = agent.invoke([
    HumanMessage(content='What meetings do I have tomorrow?')
])
print(response.response)
```

## Where MCP Tools Run (the Trust Boundary)

This is the most important thing to understand before you wire up a server: **MCP
tools execute in *your* environment, not on mAIvn's servers.**

- A `stdio` server launches as a local subprocess on your machine.
- An `http` server is an endpoint you connect to and control.

The mAIvn runtime only ever receives the tool *schemas* — names, descriptions, and
argument shapes — and orchestrates *which* tool the agent should call. The call itself
happens on your side. Your provider credentials and the data the tools touch never
leave your environment.

That boundary is also why authentication to an external server is entirely yours to
manage (see [Authenticating to the External Server](#authenticating-to-the-external-server)):
the credentials are *your* credentials for *that* provider and are unrelated to the
mAIvn `api_key` you pass to the SDK.

## Choosing a Transport

`MCPServer` supports two transports, and the choice is about where the server lives:

- **`stdio`** (the default) — launch a local subprocess and talk to it over
  stdin/stdout. Use this for servers you run on the same machine as your agent.
  Requires either a `command` to run or an `auto_setup` block.
- **`http`** — connect to a long-running endpoint you control. Use this for a service
  that runs elsewhere, or one shared across several agents. Requires a `url`.

From the SDK's perspective the two are interchangeable once connected — the agent calls
the tools the same way regardless of transport. You can register one server or several
at once and mix transports freely; the [examples page](../examples/mcp.md) shows a
single agent driving both at once.

Misconfigured connections fail fast at construction rather than at the first tool call:
`MCPServer(...)` raises `ValueError` when, for example, an `http` server is missing its
`url`, a `stdio` server has neither `command` nor `auto_setup`, the `transport` is
unrecognized, or a rate limit is not positive. A typo surfaces immediately.

> **Set a timeout on stdio servers.** If you leave `stdio_response_timeout_seconds`
> unset, the SDK warns you: a stalled subprocess could otherwise block forever. Pick a
> sensible per-call ceiling.

### Installing third-party servers on demand

Many MCP servers are distributed as Python packages you would normally install and
launch yourself. An `MCPAutoSetup` block lets the SDK resolve and run the package for
you (via `uvx`, into an isolated environment), so you do not hand-write `command` and
`args` and you do not pollute your global Python. Auto-setup is only valid for `stdio`
servers, and the `uvx` binary must be on your `PATH`. See the
[API reference](../api/mcp.md#mcpautosetup) for the fields and the
[examples page](../examples/mcp.md) for a working fetch-server setup.

## Naming and Reframing the Server's Tools

When the SDK discovers a server's tools it gives each one a **prefixed** name so tools
from different servers (and your own functions) never collide. By default the prefix is
the server's `name`, joined with `__` — a server named `tools` exposing `read_file`
surfaces as `tools__read_file`. You can change the prefix and separator, or drop the
prefix entirely to keep the server's original tool names. The final names are sanitized
to stay within the provider tool-name rules, so they are always safe to expose to the
model.

A generic MCP tool sometimes needs app-specific framing — a clearer name, a tighter
description, default arguments, or a stricter output contract. You reframe it at
registration time with the same
`ToolOverride` shape you use for your own tools via `add_tool(..., override=...)`,
keyed by the server's **raw** tool name (before prefixing). An override key that does
not match any tool the server reports raises `ValueError`, so a renamed tool never
silently leaves dead configuration behind. Defaults you set never beat the model —
model-supplied arguments win at execution time. `ToolOverride(output_schema=...)`
replaces the MCP server's advertised `outputSchema` when the server omits one or
reports a looser schema than your app expects.

See [Tools and Dependencies](tools.md) for how `ToolOverride` works across both MCP and
your own functions, and the [API reference](../api/mcp.md#tool-overrides) for the exact
merge semantics of each field.

## Authenticating to the External Server

The credentials for an external MCP server are *your* credentials for *that* provider —
unrelated to your mAIvn `api_key`, and they stay in your environment. How you present
them depends on the transport.

**HTTP servers** take credentials through `headers`, typically a bearer token. Read the
token from the environment (or your secret manager) rather than hard-coding it; any
other headers the provider expects — API keys, tenant identifiers — go in the same
dictionary.

```python
import os
from maivn import MCPServer

MCPServer(
    name='calendar',
    transport='http',
    url='https://mcp.example.com/api',
    headers={'Authorization': f'Bearer {os.environ["CALENDAR_TOKEN"]}'},
)
```

**stdio servers** read credentials from the subprocess environment. By default the SDK
launches the subprocess with only a **minimal runtime baseline** (such as `PATH`), so a
third-party server does not silently inherit every secret in your shell. You opt the
credentials in explicitly — either by setting them with `env`, or by forwarding named
variables from the parent process with `inherit_env_allowlist`:

```python
from maivn import MCPServer

# Pass exactly the variables the server needs...
MCPServer(
    name='local_tools',
    transport='stdio',
    command='python',
    args=['-m', 'my_mcp_server'],
    env={'PROVIDER_API_KEY': 'explicit-token'},
)

# ...or forward specific variables from the parent environment
MCPServer(
    name='local_tools',
    transport='stdio',
    command='python',
    args=['-m', 'my_mcp_server'],
    inherit_env_allowlist=['PROVIDER_API_KEY'],
)
```

Set `inherit_env=True` only for trusted subprocesses that genuinely need your full
shell environment.

## When an MCP Call Fails

There are two failure shapes, and the SDK treats them differently:

- **Hard errors** — the tool fails outright. By default the result is returned with an
  error flag so the agent can react; set `raise_on_tool_error=True` on the `MCPServer`
  to raise a `ValueError` instead.
- **Soft errors** — the provider returns an HTTP `200 OK` but tucks a failure (a
  rate-limit note, a quota message, a plan restriction) *inside* the response body.
  Because the transport succeeded, this would otherwise look like a valid result.
  Enabling `MCPSoftErrorHandling` teaches the SDK to recognize these by scanning the
  structured response, mark the result as an error, and — if retries remain — wait with
  exponential backoff before trying again.

Soft-error handling pairs naturally with rate limiting: set `max_calls_per_minute` or
`max_calls_per_day` to protect a downstream API and the SDK paces calls locally rather
than firing them in a burst. See the [API reference](../api/mcp.md#soft-error-handling)
for the retry/backoff fields and the scanned keys, and the
[examples page](../examples/mcp.md) for a configured retry block.

## Lifecycle

Connections close automatically when the agent closes — `agent.close()` shuts down
every registered MCP server (and its subprocesses and sockets) for you. You can also
inspect or release them directly:

```python
for server in agent.list_mcp_servers():
    print(server.name, server.transport)

agent.close_mcp_servers()  # close just the MCP connections, earlier than agent.close()
```

In a long-running service, attach `close()` to the process lifecycle — for example a
FastAPI shutdown hook — so subprocesses are not orphaned. The
[examples page](../examples/mcp.md) shows the `try/finally` pattern.

## Related

- [MCP API Reference](../api/mcp.md) — every `MCPServer`, `MCPAutoSetup`, and
  `MCPSoftErrorHandling` field, the full parameter tables, and the tool-name mapping
  and override semantics.
- [MCP Examples](../examples/mcp.md) — runnable stdio, HTTP, multi-server, `uvx`,
  rate-limit, and structured-output setups you can copy.
- [Tools and Dependencies](tools.md) — how MCP tools sit alongside your own functions,
  and how `ToolOverride` reframes any tool for one app.
- [Multi-Agent](multi-agent.md) — register MCP servers on the agents inside a `Swarm`.
- [Core Concepts](../core-concepts.md) — the scope/tool mental model that MCP plugs
  into.
- [SDK Overview](../overview.md) — start here if MCP is your first stop in the SDK.
