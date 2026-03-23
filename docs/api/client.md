# Client

The `Client` class manages HTTP connections to the maivn server. It handles authentication, thread management, timeout configuration, and programmatic resource management.

Most users won't interact with `Client` directly - it's auto-created when you provide `api_key` to an `Agent`. However, explicit `Client` usage enables connection reuse across multiple agents.

## Import

```python
from maivn import Client, ClientBuilder
```

## Constructor

```python
Client(
    api_key: str | None = None,
    *,
    client_timezone: str | None = None,
    auto_detect_timezone: bool = True,
    timeout: int | float | None = None,
    thread_id: str | None = None,
    tool_execution_timeout: float | None = None,
    dependency_wait_timeout: float | None = None,
    total_execution_timeout: float | None = None,
)
```

### Parameters

| Parameter                 | Type                   | Default | Description                                |
| ------------------------- | ---------------------- | ------- | ------------------------------------------ |
| `api_key`                 | `str \| None`          | `None`  | API key for authentication                 |
| `client_timezone`         | `str \| None`          | `None`  | IANA timezone (e.g., `'America/New_York'`) |
| `auto_detect_timezone`    | `bool`                 | `True`  | Auto-detect system timezone                |
| `timeout`                 | `int \| float \| None` | `None`  | HTTP request timeout (seconds)             |
| `thread_id`               | `str \| None`          | `None`  | Initial thread ID                          |
| `tool_execution_timeout`  | `float \| None`        | `None`  | Per-tool timeout (seconds)                 |
| `dependency_wait_timeout` | `float \| None`        | `None`  | Dependency resolution timeout              |
| `total_execution_timeout` | `float \| None`        | `None`  | Total session timeout                      |

### Timeout Hierarchy

| Timeout                   | Default         | Description                        |
| ------------------------- | --------------- | ---------------------------------- |
| `tool_execution_timeout`  | 900s (15 min)   | Max time for each tool/LLM call    |
| `dependency_wait_timeout` | 300s            | Max wait for upstream dependencies |
| `total_execution_timeout` | 7200s (2 hours) | Max total session duration         |

### Timezone Configuration

The maivn system has built-in datetime awareness. The timezone parameters control how the system interprets time-related queries:

| Parameter                             | Behavior                                                     |
| ------------------------------------- | ------------------------------------------------------------ |
| `auto_detect_timezone=True` (default) | SDK detects your system timezone automatically               |
| `client_timezone='America/New_York'`  | Explicit timezone overrides auto-detection                   |
| `auto_detect_timezone=False`          | Disables auto-detection; requires explicit `client_timezone` |

**Resolution order:**

1. If `client_timezone` is set, use that timezone
2. If `auto_detect_timezone=True` and `client_timezone` is not set, detect system timezone
3. If both are disabled/unset, the server uses its deployment timezone

```python
# Auto-detect (default behavior)
client = Client(api_key='...')

# Explicit timezone
client = Client(
    api_key='...',
    client_timezone='Europe/London',
)

# Disable auto-detection, use server default
client = Client(
    api_key='...',
    auto_detect_timezone=False,
)
```

See [System Tools Guide](../guides/system-tools.md) for more on datetime awareness.

## Methods

### Thread Management

#### set_thread_id()

Set the thread ID for session continuity.

```python
def set_thread_id(thread_id: str) -> None
```

#### new_thread_id()

Generate and set a new UUID4 thread ID.

```python
def new_thread_id() -> str
```

#### get_thread_id()

Get the current thread ID.

```python
def get_thread_id(create_if_missing: bool = False) -> str | None
```

### Timeout Resolution

#### get_tool_execution_timeout()

Get effective tool execution timeout.

```python
def get_tool_execution_timeout() -> float
```

#### get_dependency_wait_timeout()

Get effective dependency wait timeout.

```python
def get_dependency_wait_timeout() -> float
```

#### get_total_execution_timeout()

Get effective total execution timeout.

```python
def get_total_execution_timeout() -> float | None
```

### Redaction Preview

#### preview_redaction()

Preview server-side redaction without starting a full session.

```python
def preview_redaction(
    *,
    payload: RedactionPreviewRequest | dict[str, Any],
) -> RedactionPreviewResponse
```

Pass either a `RedactionPreviewRequest` model or an equivalent dictionary payload. The client sends the request to `/preview-redaction` and validates the response as `RedactionPreviewResponse`.

Example:

```python
from maivn import Client, RedactedMessage, RedactionPreviewRequest

client = Client(api_key='...')

preview = client.preview_redaction(
    payload=RedactionPreviewRequest(
        message=RedactedMessage(content='Contact alice@example.com'),
        known_pii_values=['alice@example.com'],
    )
)

assert preview.redacted_value_count == 1
```

### Memory Resource Management

Use `Client` when you want to manage memory resources outside of an invocation flow.

Organization governance methods:

- `get_organization_memory_policy(org_id)`
- `update_organization_memory_policy(org_id, policy)`
- `purge_organization_memory(org_id, *, project_id=None, session_id=None)`

Project resource methods:

- `list_project_memory_resources(project_id)`
- `list_memory_skills(project_id, ...)`
- `create_memory_skill(project_id, payload)`
- `update_memory_skill(project_id, skill_id, payload)`
- `delete_memory_skill(project_id, skill_id)`
- `list_memory_insights(project_id, ...)`
- `create_memory_insight(project_id, payload)`
- `update_memory_insight(project_id, insight_id, payload)`
- `promote_memory_insight(project_id, insight_id, *, target_scope)`
- `delete_memory_insight(project_id, insight_id)`
- `list_memory_resources(project_id, ...)`
- `get_memory_resource(project_id, resource_id)`
- `create_memory_resource(project_id, payload)`
- `update_memory_resource(project_id, resource_id, payload)`
- `replace_memory_resource(project_id, resource_id, payload)`
- `bind_memory_resource(project_id, resource_id, *, binding_type, target_id)`
- `restore_memory_resource(project_id, resource_id)`
- `rebind_memory_resource_to_portal(project_id, resource_id)`
- `delete_memory_resource(project_id, resource_id)`
- `list_unbound_memory_resource_candidates(project_id, ...)`

Compatibility note:

- use the `resource`-named methods for new SDK code
- document-named methods remain available as compatibility aliases over the same backend contracts

Example:

```python
client = Client(api_key="your-api-key")

policy = client.get_organization_memory_policy("org_123")

resources = client.list_project_memory_resources("project_456")

resource = client.create_memory_resource(
    "project_456",
    {
        "name": "deploy-runbook.txt",
        "mime_type": "text/plain",
        "content_base64": "U29tZSBiYXNlNjQgY29udGVudA==",
        "sharing_scope": "org",
    },
)
```

### Resource Management

#### close()

Close HTTP connections.

```python
def close() -> None
```

## Properties

| Property              | Type            | Description          |
| --------------------- | --------------- | -------------------- |
| `api_key`             | `str \| None`   | The API key          |
| `timeout`             | `float \| None` | HTTP request timeout |
| `client_timezone`     | `str \| None`   | Client timezone      |
| `deployment_timezone` | `str`           | Server timezone      |
| `thread_id`           | `str \| None`   | Current thread ID    |
| `base_url`            | `str`           | Server API base URL  |

## Context Manager

Client supports the context manager protocol:

```python
with Client(api_key='...') as client:
    agent = Agent(name='my_agent', client=client)
    response = agent.invoke([...])
# HTTP connections automatically closed
```

## Examples

### Shared Client Across Agents

```python
# Create one client for multiple agents
client = Client(
    api_key='your-api-key',
    tool_execution_timeout=600,  # 10 minutes
)

agent1 = Agent(name='agent1', client=client)
agent2 = Agent(name='agent2', client=client)

# Both agents share the same connection pool
```

### Multi-Turn Conversation

```python
client = Client(api_key='your-api-key')
thread_id = client.new_thread_id()

agent = Agent(name='chat', client=client)

# First turn
response1 = agent.invoke(
    [HumanMessage(content='Hello')],
    thread_id=thread_id,
)

# Second turn (same thread)
response2 = agent.invoke(
    [HumanMessage(content='Follow up question')],
    thread_id=thread_id,
)
```

### Custom Timeouts

```python
client = Client(
    api_key='your-api-key',
    timeout=60,  # HTTP timeout
    tool_execution_timeout=900,  # 15 min per tool
    dependency_wait_timeout=120,  # 2 min for deps
    total_execution_timeout=3600,  # 1 hour total
)
```

## ClientBuilder

Factory for creating `Client` instances.

### from_environment()

Create a client using environment variables:

```python
client = ClientBuilder.from_environment()
```

Uses these environment variables:

- `MAIVN_API_KEY` - API key
- `MAIVN_TIMEOUT` - HTTP timeout
- `MAIVN_TOOL_EXECUTION_TIMEOUT` - Per-tool timeout
- `MAIVN_DEPENDENCY_WAIT_TIMEOUT` - Dependency timeout
- `MAIVN_TOTAL_EXECUTION_TIMEOUT` - Total timeout

### from_configuration()

Create a client from a `MaivnConfiguration`:

```python
from maivn import ConfigurationBuilder, ClientBuilder

config = ConfigurationBuilder.from_environment()
client = ClientBuilder.from_configuration(config)
```

## Client Caching

When you provide `api_key` to an `Agent`, the SDK caches `Client` instances by:

- API key
- Base URL
- Timeout

This means multiple agents with the same `api_key` share the same `Client` automatically.

## See Also

- [Agent](agent.md) - Main agent class
- [Configuration](configuration.md) - Configuration system
