# Best Practices

Recommended patterns for building robust maivn agents in production.

## Agent Design

### Clear Naming

Use descriptive names that reflect purpose:

```python
# Good
agent = Agent(
    name='customer_support_agent',
    description='Handles customer inquiries and support tickets',
)

# Avoid
agent = Agent(name='agent1')
```

### Effective System Prompts

Write system prompts that guide behavior:

```python
agent = Agent(
    name='data_analyst',
    system_prompt='''You are a data analyst assistant.

    Your capabilities:
    - Analyze datasets using the analyze_data tool
    - Generate reports using the create_report tool
    - Answer questions about data trends

    Guidelines:
    - Always validate data before analysis
    - Explain your methodology clearly
    - Provide confidence levels for predictions
    - Use the final_report tool for all final outputs''',
    api_key='...',
)
```

### Focused Agents

Each agent should have a clear, focused responsibility:

```python
# Good: focused agents
researcher = Agent(name='researcher', description='Finds information')
analyst = Agent(name='analyst', description='Analyzes data')
writer = Agent(name='writer', description='Writes content')

# Avoid: unfocused agent
general = Agent(name='everything', description='Does all tasks')
```

## Tool Design

### Choose the Registration Style That Fits the Module

Use `@agent.toolify(...)` when the tool naturally belongs next to the agent definition:

```python
@agent.toolify(description='Fetch customer orders')
def get_orders(customer_id: str) -> dict:
    ...
```

Use `Agent(..., tools=[...])` when tools are already defined and you want the agent
configuration to show its complete tool surface:

```python
def get_orders(customer_id: str) -> dict:
    """Fetch customer orders."""
    ...

agent = Agent(
    name='orders',
    api_key='...',
    tools=[get_orders],
)
```

Use `agent.add_tool(...)` when tools are assembled conditionally or imported from another
module:

```python
agent = Agent(name='orders', api_key='...')
agent.add_tool(get_orders, description='Fetch customer orders')
```

All three styles use the same underlying tool registry and dependency decorators.

### Clear Descriptions

Tool descriptions help the LLM decide when to use them:

```python
# Good: specific and actionable
@agent.toolify(description='Search product catalog by name, SKU, or category')
def search_products(query: str) -> dict: ...

# Avoid: vague
@agent.toolify(description='Search')
def search_products(query: str) -> dict: ...
```

### Type Hints and Defaults

Use specific types and sensible defaults:

```python
from typing import Literal
from pydantic import Field

@agent.toolify(description='Fetch customer orders')
def get_orders(
    customer_id: str,
    status: Literal['pending', 'shipped', 'delivered'] | None = None,
    limit: int = Field(default=10, ge=1, le=100),
) -> dict:
    ...
```

### Focused Tools

One tool should do one thing well:

```python
# Good: separate focused tools
@agent.toolify()
def fetch_user(user_id: str) -> dict: ...

@agent.toolify()
def update_user(user_id: str, data: dict) -> dict: ...

@agent.toolify()
def delete_user(user_id: str) -> dict: ...

# Avoid: multi-purpose tool
@agent.toolify()
def manage_user(action: str, user_id: str, data: dict = None) -> dict: ...
```

### Error Returns

Return structured errors instead of raising exceptions:

```python
@agent.toolify()
def fetch_data(source: str) -> dict:
    try:
        data = external_api.fetch(source)
        return {'status': 'success', 'data': data}
    except NotFoundError:
        return {'status': 'error', 'error': f'Source not found: {source}'}
    except PermissionError:
        return {'status': 'error', 'error': 'Permission denied'}
```

## Dependency Management

### Parallel Where Possible

Design for parallel execution:

```python
# Good: independent tools run in parallel
@agent.toolify()
def fetch_users() -> dict: ...

@agent.toolify()
def fetch_products() -> dict: ...

@agent.toolify()
@depends_on_tool(fetch_users, 'users')
@depends_on_tool(fetch_products, 'products')  # Both run in parallel
def generate_report(users: dict, products: dict) -> dict: ...
```

## Security

### Private Data for Secrets

Never hardcode secrets:

```python
# Good: use private data
agent.private_data = {'api_key': os.environ['API_KEY']}

@agent.toolify()
@depends_on_private_data(data_key='api_key', arg_name='key')
def call_api(key: str) -> dict: ...

# Avoid: hardcoded secrets
@agent.toolify()
def call_api() -> dict:
    api_key = 'sk-xxx-hardcoded'  # Never do this!
    ...
```

### Minimal Private Data

Only include what's needed:

```python
# Good: only what's needed
agent.private_data = {
    'db_connection_string': '...',  # Used by database tool
}

# Avoid: everything "just in case"
agent.private_data = {
    'db_connection_string': '...',
    'unused_secret_1': '...',
    'unused_secret_2': '...',
}
```

### Match EventBridge Audience to the Frontend

Treat browser event streams as a trust boundary:

```python
from maivn.events import EventBridge

# End-user browser UI
public_bridge = EventBridge('session-1', audience='frontend_safe')

# Internal developer/admin UI
internal_bridge = EventBridge('session-1', audience='internal')
```

Use `frontend_safe` when the event stream reaches end users. Use `internal` only for trusted tooling such as mAIvn Studio, Booth, or your own internal operator consoles.

For the easiest possible end-to-end setup, use the FastAPI adapter — one line wires `GET /maivn/events/{session_id}` and your frontend (in any language) can consume it via `EventSource` or any SSE client:

```python
from fastapi import FastAPI
from maivn.events.fastapi import mount_events

app = FastAPI()
mount_events(app, factory=lambda sid: EventBridge(sid, audience='frontend_safe'))
```

See the [frontend events guide](guides/frontend-events.md) for client examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python, Rust, and more.

### Harden Third-Party stdio MCP Servers

If you launch third-party MCP servers over stdio, prefer explicit environment passing instead of inheriting your full backend environment:

```python
mcp_server = MCPServer(
    name='external_tools',
    transport='stdio',
    command='python',
    args=['-m', 'my_mcp_server'],
    inherit_env=False,
    inherit_env_allowlist=['OPENAI_API_KEY'],
    env={'SERVICE_TOKEN': 'explicit-token'},
    stdio_response_timeout_seconds=30,
)
```

Keep `inherit_env=True` for compatibility when you trust the subprocess and want the simplest setup. Tighten it for third-party tools, demos, or mixed-trust environments.

## Error Handling

### Graceful Degradation

Handle errors without crashing:

```python
@agent.toolify()
def fetch_with_fallback(primary_source: str) -> dict:
    try:
        return {'data': fetch_from_primary(primary_source)}
    except PrimarySourceError:
        try:
            return {'data': fetch_from_backup(), 'source': 'backup'}
        except BackupError:
            return {'error': 'All sources unavailable'}
```

### Informative Errors

Return errors that help debugging:

```python
@agent.toolify()
def process_data(data: dict) -> dict:
    if 'required_field' not in data:
        return {
            'error': 'Missing required_field',
            'received_fields': list(data.keys()),
        }
    ...
```

## Performance

### Timeout Configuration

Set appropriate timeouts:

```python
client = Client(
    api_key='...',
    tool_execution_timeout=900,   # 15 min per tool
    dependency_wait_timeout=300,  # 5 min for deps
    total_execution_timeout=7200, # 2 hours total
)
```

### Limit Results

Use `max_results` for large tool catalogs (limits semantic search only; final/targeted tools and dependencies can add more):

```python
agent = Agent(
    name='large_catalog_agent',
    max_results=15,  # Limit semantic search results
    api_key='...',
)
```

### Resource Cleanup

Clean up resources when done:

```python
# Using context manager
with Agent(name='temp', api_key='...') as agent:
    response = agent.invoke([...])
# Resources cleaned up automatically

# Or explicit cleanup
agent = Agent(name='temp', api_key='...')
try:
    response = agent.invoke([...])
finally:
    agent.close()
```

## Testing

### Unit Test Tools in Isolation

```python
def test_fetch_data():
    result = fetch_data('test-source')
    assert result['status'] == 'success'
    assert 'data' in result
```

### Mock External Services

```python
from unittest.mock import patch

def test_api_tool():
    with patch('module.external_api') as mock_api:
        mock_api.return_value = {'result': 'mocked'}
        result = call_api(key='test-key')
        assert result['status'] == 'success'
```

### Test Dependency Resolution

```python
def test_dependency_chain():
    # Verify tool A's output flows to tool B
    a_result = tool_a()
    b_result = tool_b(a_result)
    assert b_result['processed_from'] == a_result
```

## Logging

### Enable in Development

```python
from maivn import configure_logging

logger = configure_logging('logs/dev.log')
logger.setLevel('DEBUG')
```

### Use Event Builder for Live Tracing

```python
response = agent.events().invoke(messages)
```

### Don't Log Secrets

```python
# Good
logger.info(f'Processing request for user {user_id}')

# Avoid
logger.info(f'Using API key {api_key}')  # Never log secrets!
```

## Swarm Design

### Clear Responsibilities

Each agent in a swarm should have a distinct role:

```python
swarm = Swarm(
    name='content_team',
    agents=[
        Agent(name='researcher', description='Finds information'),
        Agent(name='analyst', description='Analyzes findings'),
        Agent(name='writer', description='Writes final content'),
    ],
)
```

### Single Final Output

Designate exactly one source of final output:

```python
# Option 1: final output agent
writer = Agent(name='writer', use_as_final_output=True)

# Option 2: swarm-level final tool
@swarm.toolify(final_tool=True)
class TeamReport(BaseModel): ...
```

### Minimal Agent Count

Use the minimum number of agents needed:

```python
# Good: 3 agents for clear separation of concerns
researcher -> analyst -> writer

# Avoid: unnecessary agents
researcher -> summarizer -> analyst -> formatter -> writer -> editor
```

## See Also

- [Troubleshooting](troubleshooting.md) - Solving common issues
- [API Reference](api/README.md) - Complete API documentation
- [Guides](guides/getting-started.md) - Step-by-step tutorials
