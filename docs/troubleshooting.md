# Troubleshooting

Common errors and solutions when using the maivn SDK.

## Common Errors

### Agent requires either a Client instance or an api_key

**Error:**
```
ValueError: Agent requires either a Client instance or an api_key.
```

**Cause:** You created an Agent without providing authentication.

**Solution:**
```python
# Option 1: Provide api_key
agent = Agent(name='my_agent', api_key='your-api-key')

# Option 2: Use environment variable
# Set MAIVN_API_KEY in your environment
agent = Agent(name='my_agent', api_key=os.environ.get('MAIVN_API_KEY'))

# Option 3: Provide a Client
client = Client(api_key='your-api-key')
agent = Agent(name='my_agent', client=client)
```

### force_final_tool and targeted_tools are mutually exclusive

**Error:**
```
ValueError: force_final_tool and targeted_tools are mutually exclusive.
```

**Cause:** You specified both options in `invoke()`.

**Solution:** Choose one or the other:
```python
# Option 1: Force final tool
response = agent.invoke(messages, force_final_tool=True)

# Option 2: Target specific tools
response = agent.invoke(messages, targeted_tools=['tool_a', 'tool_b'])
```

### Multiple tools marked with final_tool=True

**Error:**
```
TOOL CONFIGURATION ERROR
================================================================================
[ERROR] Multiple tools marked with final_tool=True: 'Report', 'Summary'
  SCOPE: Agent 'my_agent'
  ISSUE: Only ONE tool can be designated as the final output tool.
  FIX: Remove 'final_tool=True' from all but one tool.
================================================================================
```

**Cause:** More than one tool is marked as the final output.

**Solution:** Keep only one final tool:
```python
@agent.toolify(final_tool=True)
class Report(BaseModel): ...

@agent.toolify()  # Remove final_tool=True
class Summary(BaseModel): ...
```

### Argument not found in function signature

**Error:**
```
ValueError: Argument 'data' specified in dependency decorator
not found in function 'my_tool' signature: (result: dict) -> dict
```

**Cause:** The `arg_name` in a decorator doesn't match the function parameter.

**Solution:** Use the correct parameter name:
```python
# Wrong: arg_name doesn't match
@depends_on_tool(other_tool, arg_name='data')
def my_tool(result: dict) -> dict: ...

# Correct: arg_name matches parameter
@depends_on_tool(other_tool, arg_name='result')
def my_tool(result: dict) -> dict: ...
```

### force_final_tool requires at least one tool with final_tool=True

**Error:**
```
ValueError: force_final_tool=True requires at least one tool with final_tool=True.
Agent 'my_agent' has 3 tool(s) but none are final.
```

**Cause:** You used `force_final_tool=True` but no tool is marked as final.

**Solution:** Mark one tool as final:
```python
@agent.toolify(final_tool=True)  # Add final_tool=True
class FinalOutput(BaseModel):
    result: str

# Or register the final model imperatively.
agent.add_tool(FinalOutput, final_tool=True)
```

If you use constructor-based tools, raw functions and models are registered with default
tool options. Use `agent.add_tool(..., final_tool=True)` when a Pydantic model must be the
agent's final tool.

### Connection errors

**Error:**
```
httpx.ConnectError: [Errno 111] Connection refused
```

**Cause:** Cannot connect to the maivn server.

**Solutions:**
1. Check that the maivn server is running
2. Verify the server URL in configuration
3. Check network connectivity
4. Verify firewall rules

### Timeout errors

**Error:**
```
httpx.ReadTimeout: timed out
```

**Cause:** Request took longer than the timeout.

**Solutions:**
```python
# Increase timeout
client = Client(
    api_key='...',
    timeout=120,  # HTTP timeout
    tool_execution_timeout=600,  # Per-tool timeout
    total_execution_timeout=3600,  # Total session timeout
)
```

### MCP server startup failure

**Error:**
```
ValueError: STDIO MCPServer requires a command or auto_setup
```

**Cause:** MCP server configuration is incomplete.

**Solution:**
```python
# Provide command
MCPServer(
    name='my_server',
    transport='stdio',
    command='my-mcp-server',  # Add this
)

# Or use auto_setup
MCPServer(
    name='my_server',
    transport='stdio',
    auto_setup=MCPAutoSetup(package='my-mcp-package'),
)
```

### Memory retrieval returns no hits

**Symptoms:**
- Follow-up turns do not recall prior details
- `memory_retrieved` appears with low/zero hit count

**Checks:**
1. Reuse the same `thread_id` across turns.
2. Confirm memory is enabled (`memory_config.enabled=True`, or omit it and rely on scope defaults).
3. Confirm retrieval is enabled via `memory_config.level` (`glimpse`, `focus`, or `clarity`).
4. Verify org/project policy does not downscope memory behavior.
5. If testing immediately after a seed turn, wait briefly and retry (indexing is async).

### `memory_indexed` not observed

**Cause (common):**
- Persistence not enabled at effective policy level, or write path was downscoped by config.

**Checks:**
1. Confirm `memory_config.level` supports persistence (`focus` or `clarity`).
2. Confirm `memory_config.level` is not downscoped by policy.
3. Confirm workspace policy permits requested persistence mode.
4. Inspect enrichment stream for `memory_indexing`/`memory_indexed` events.

### Context still too large in long threads

**Checks:**
1. Ensure `memory_config.summarization_enabled` is not disabled for the run.
2. Keep prompts focused and avoid repeated full-history payloads when not needed.
3. Use event tracing (`agent.events().invoke(...)`) to verify summarize phases are emitted.

## Debugging

### Enable Event Tracing

See detailed execution information:
```python
response = agent.events().invoke(messages)
```

### Configure Logging

Set up file logging:
```python
from pathlib import Path
from maivn import configure_logging

log_file = Path('logs/maivn.log')
log_file.parent.mkdir(exist_ok=True)

logger = configure_logging(log_file)
```

### Check Log Level

Set debug level for more detail:
```bash
export MAIVN_LOG_LEVEL=DEBUG
```

### Inspect Tool Registration

List registered tools:
```python
for tool in agent.list_tools():
    print(f'{tool.name}: {tool.description}')
    print(f'  final_tool: {getattr(tool, "final_tool", False)}')
    print(f'  dependencies: {getattr(tool, "dependencies", [])}')
```

### Compile State Without Executing

See what would be sent to the server:
```python
state = agent.compile_state(messages)
print(f'Tools: {len(state.tools)}')
print(f'Private data keys: {list(state.private_data.keys())}')
```

## FAQ

### How do I reuse a thread for multi-turn conversation?

```python
# First turn
response1 = agent.invoke(
    [HumanMessage(content='Hello')],
    thread_id='my-conversation',
)

# Second turn (same thread_id)
response2 = agent.invoke(
    [HumanMessage(content='Follow up')],
    thread_id='my-conversation',
)
```

### Why isn't my tool being called?

1. **Check the description** - Make it clear when the tool should be used
2. **Check the system prompt** - Guide the LLM to use your tools
3. **Use event tracing** - Run with `agent.events().invoke(...)` to inspect decisions
4. **Check tool registration** - Use `agent.list_tools()`

### How do I handle large tool catalogs?

Use `max_results` to limit semantic search (final/targeted tools and dependencies may increase the total tool count):
```python
agent = Agent(
    name='large_catalog',
    max_results=10,  # Only return top 10 matching tools
    api_key='...',
)
```

### How do I cancel a running invocation?

Currently, cancellation must be done at the server level. The SDK doesn't support client-side cancellation.

### Why are my private data values appearing in logs?

They shouldn't be. If you see this:
1. Check that you're using `@depends_on_private_data` correctly
2. Ensure you're not manually logging the values in your tools
3. Report this as a potential bug if automatic redaction isn't working

## Environment Checklist

Before debugging, verify:

- [ ] `MAIVN_API_KEY` (or `MAIVN_DEV_API_KEY` for development) is set
- [ ] maivn server is running and accessible
- [ ] Network connectivity to server
- [ ] Correct Python version (3.11+)
- [ ] All dependencies installed (`uv sync` or `pip install maivn`)

## Getting Help

If you're still stuck:

1. Check the [API Reference](api/README.md)
2. Review the [Guides](guides/getting-started.md)
3. Run a known-good app in [mAIvn Studio](guides/maivn-studio.md) and compare event traces
4. Inspect a failing run from the **Executions** explorer in the developer portal

## See Also

- [Logging Reference](api/logging.md) - SDK logging
- [Configuration Reference](api/configuration.md) - Environment variables
- [Best Practices](best-practices.md) - Recommended patterns
