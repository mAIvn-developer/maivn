# Agent

The `Agent` class is the primary interface for building agentic systems with maivn. It acts as a container for tools, configuration, and invocation logic.

## Import

```python
from maivn import Agent
```

## Constructor

```python
Agent(
    name: str | None = None,
    description: str | None = None,
    system_prompt: str | SystemMessage | None = None,
    api_key: str | None = None,
    client: Client | None = None,
    timeout: float | None = None,
    max_results: int | None = None,
    tools: list[Any] = [],
    use_as_final_output: bool = False,
    included_nested_synthesis: bool | Literal['auto'] = 'auto',
    private_data: dict = {},
    memory_config: MemoryConfig | dict[str, Any] = {},
    skills: list[dict[str, Any]] = [],
    resources: list[dict[str, Any]] = [],
    tags: list[str] = [],
    before_execute: Callable | None = None,
    after_execute: Callable | None = None,
    hook_execution_mode: Literal['tool', 'scope', 'agent'] = 'tool',
)
```

### Parameters

| Parameter                   | Type                             | Default    | Description                                                                                                                                                            |
| --------------------------- | -------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                      | `str \| None`                    | Class name | Agent name (used in logs and identification)                                                                                                                           |
| `description`               | `str \| None`                    | `None`     | Human-readable description of agent purpose                                                                                                                            |
| `system_prompt`             | `str \| SystemMessage \| None`   | `None`     | System message injected into conversations                                                                                                                             |
| `api_key`                   | `str \| None`                    | `None`     | API key for server authentication                                                                                                                                      |
| `client`                    | `Client \| None`                 | `None`     | Existing Client instance (alternative to api_key)                                                                                                                      |
| `timeout`                   | `float \| None`                  | `None`     | Default timeout in seconds                                                                                                                                             |
| `max_results`               | `int \| None`                    | `None`     | Max tools returned from semantic search (final/targeted tools and dependencies may add more)                                                                           |
| `tools`                     | `list[Any]`                      | `[]`       | Initial tools to register on this agent. Accepts callables, Pydantic model classes, and prebuilt SDK tool objects                                                      |
| `use_as_final_output`       | `bool`                           | `False`    | When in a Swarm, designate this agent's output as final                                                                                                                |
| `included_nested_synthesis` | `bool \| Literal['auto']`        | `'auto'`   | Nested synthesis mode for Swarm invocations: `True` always includes synthesized response, `False` returns tool results only, `'auto'` lets orchestrator/runtime decide |
| `private_data`              | `dict`                           | `{}`       | Server-side secret data for dependency injection                                                                                                                       |
| `memory_config`             | `MemoryConfig \| dict[str, Any]` | `{}`       | Default typed memory configuration applied on every invocation from this scope                                                                                         |
| `skills`                    | `list[dict[str, Any]]`           | `[]`       | Optional user-defined memory skill payloads merged into invocation metadata                                                                                            |
| `resources`                 | `list[dict[str, Any]]`           | `[]`       | Optional bound resource payloads merged into invocation metadata                                                                                                       |
| `tags`                      | `list[str]`                      | `[]`       | Tags for organization and filtering                                                                                                                                    |
| `before_execute`            | `Callable \| None`               | `None`     | Hook called before execution                                                                                                                                           |
| `after_execute`             | `Callable \| None`               | `None`     | Hook called after execution                                                                                                                                            |
| `hook_execution_mode`       | `Literal`                        | `'tool'`   | When hooks fire: `'tool'`, `'scope'`, or `'agent'`                                                                                                                     |

### Requirements

You must provide either `api_key` or `client`:

```python
# Option 1: API key (Client auto-created)
agent = Agent(name='my_agent', api_key='your-api-key')

# Option 2: Explicit Client
client = Client(api_key='your-api-key')
agent = Agent(name='my_agent', client=client)
```

### Swarm Nested Synthesis

`included_nested_synthesis` controls how this agent behaves when it is invoked as a nested member of a Swarm:

- `True`: always generate/include synthesized response text
- `False`: skip nested synthesis and return tool results only
- `'auto'` (default): root Swarm orchestration/runtime decides based on context and payload size

```python
planner = Agent(
    name='planner',
    api_key='...',
    included_nested_synthesis='auto',
)
```

### Memory Configuration

`memory_config` stores default typed memory behavior for this agent. You can pass a `MemoryConfig`
instance or an equivalent dictionary.

Common fields:

| Key                     | Type                            | Example                       | Purpose                                                      |
| ----------------------- | ------------------------------- | ----------------------------- | ------------------------------------------------------------ |
| `enabled`               | `bool`                          | `True`                        | Master toggle for public memory behavior                     |
| `level`                 | `str`                           | `"focus"`                     | Memory behavior level: `none`, `glimpse`, `focus`, `clarity` |
| `summarization_enabled` | `bool`                          | `True`                        | Optional summarization override                              |
| `persistence_mode`      | `str`                           | `"vector_plus_graph"`         | Optional downscope for persistence writes                    |
| `retrieval`             | `MemoryRetrievalConfig`         | `{"resources_enabled": True}` | Retrieval tuning and signal toggles                          |
| `skill_extraction`      | `MemorySkillExtractionConfig`   | `{"enabled": True}`           | Skill extraction controls                                    |
| `insight_extraction`    | `MemoryInsightExtractionConfig` | `{"enabled": True}`           | Insight extraction controls                                  |

Notes:

- Effective behavior is policy-gated server-side by workspace and plan limits.
- Per-invocation `memory_config` can override these defaults for one call.
- Reserved memory-control keys are rejected in invocation `metadata`; use `memory_config`.
- `thread_id` governs episodic recall; skills, bound resources, and promoted insights are the cross-thread reuse layer.
- `insight_extraction.sharing_scope` is limited to `agent` or `swarm` for AI-generated insights. Use portal promotion for `project` or `org`.

### Skills and Resources

`skills` and `resources` let you attach memory assets to the scope.
The SDK normalizes these payloads and injects them into invocation metadata as:

- `memory_defined_skills`
- `memory_bound_resources`

Use this for scope-bound runbooks/checklists without hand-building metadata per call.

Legacy note: `documents` remains accepted as a compatibility alias for older SDK code.

## Methods

### add_tool()

Register a callable, Pydantic model class, or prebuilt SDK tool on the agent.

```python
def add_tool(
    tool: BaseTool | Callable[..., Any] | type[BaseModel],
    name: str | None = None,
    description: str | None = None,
    *,
    always_execute: bool = False,
    final_tool: bool = False,
    tags: list[str] | None = None,
    before_execute: Callable[[dict[str, Any]], Any] | None = None,
    after_execute: Callable[[dict[str, Any]], Any] | None = None,
) -> BaseTool
```

Use `Agent(..., tools=[...])` for simple constructor registration and `add_tool(...)`
when you need options such as `name`, `description`, `tags`, or `final_tool`.

```python
def load_profile(customer_id: str) -> dict:
    """Load a customer profile."""
    return {'customer_id': customer_id}

agent = Agent(name='support', api_key='...', tools=[load_profile])

class ResolutionPlan(BaseModel):
    """Write the final support plan."""

    steps: list[str]

agent.add_tool(ResolutionPlan, name='resolution_plan', final_tool=True)
```

### invoke()

Execute the agent with messages.

```python
def invoke(
    messages: Sequence[BaseMessage],
    force_final_tool: bool = False,
    targeted_tools: list[str] | None = None,
    model: Literal['fast', 'balanced', 'max'] | None = None,
    reasoning: Literal['minimal', 'low', 'medium', 'high'] | None = None,
    thread_id: str | None = None,
    verbose: bool = False,
    metadata: dict[str, Any] | None = None,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
    allow_private_in_system_tools: bool | None = None,
) -> SessionResponse
```

#### Parameters

| Parameter                       | Type                                     | Default  | Description                                                                               |
| ------------------------------- | ---------------------------------------- | -------- | ----------------------------------------------------------------------------------------- |
| `messages`                      | `Sequence[BaseMessage]`                  | Required | Messages to send to the agent                                                             |
| `force_final_tool`              | `bool`                                   | `False`  | Return result from `final_tool=True` tool                                                 |
| `targeted_tools`                | `list[str] \| None`                      | `None`   | Run only these tools (plus dependencies)                                                  |
| `model`                         | `Literal`                                | `None`   | LLM selection hint: `'fast'`, `'balanced'`, `'max'`                                       |
| `reasoning`                     | `Literal`                                | `None`   | Reasoning level: `'minimal'` to `'high'`                                                  |
| `thread_id`                     | `str \| None`                            | `None`   | Thread ID for multi-turn conversations                                                    |
| `verbose`                       | `bool`                                   | `False`  | Legacy terminal tracing flag. Prefer `events().invoke(...)`.                              |
| `metadata`                      | `dict \| None`                           | `None`   | Additional application metadata for the session, including supported runtime control keys |
| `memory_config`                 | `MemoryConfig \| dict[str, Any] \| None` | `None`   | Per-invocation memory override merged over scope defaults                                 |
| `allow_private_in_system_tools` | `bool \| None`                           | `None`   | Optional override to allow raw private data access in system tools (advanced use only)    |

#### Returns

`SessionResponse` containing:

- `content`: The response content
- `tool_results`: Results from executed tools
- `metadata`: Response metadata

#### Common metadata keys

| Key                                 | Type                | Purpose                                                                    |
| ----------------------------------- | ------------------- | -------------------------------------------------------------------------- |
| `allowed_system_tools`              | `list[str]`         | Restrict which system tools may run for this invocation                    |
| `approved_compose_artifact_targets` | `list[str] \| bool` | Explicitly approve `compose_artifact` targets such as `tool_name.arg_name` |

#### Raises

- `ValueError`: If tool configuration is invalid
- `ValueError`: If `force_final_tool` and `targeted_tools` both specified
- `ValueError`: If `force_final_tool=True` but no `final_tool` defined

#### Examples

````python
from maivn.messages import HumanMessage

# Basic invocation
response = agent.invoke([HumanMessage(content='Hello')])

# Force structured output
response = agent.invoke(
    [HumanMessage(content='Analyze this data')],
    force_final_tool=True,
)

# Run specific tools only
response = agent.invoke(
    [HumanMessage(content='Run diagnostics')],
    targeted_tools=['fetch_data', 'analyze_data'],
)

# Multi-turn with thread ID
response = agent.invoke(
    [HumanMessage(content='Follow up question')],
    thread_id='conversation-123',
)

# Override memory for one turn
response = agent.invoke(
    [HumanMessage(content='Recall prior rollout details')],
    thread_id='conversation-123',
    memory_config={"level": "glimpse"},
)

# Restrict system tools and explicitly approve a compose_artifact target
response = agent.invoke(
    [HumanMessage(content='Compose and validate the SQL artifact')],
    force_final_tool=True,
    metadata={
        'allowed_system_tools': ['compose_artifact'],
        'approved_compose_artifact_targets': ['validate_query_artifact.query'],
    },
)

# Event tracing for debugging
response = agent.events().invoke([HumanMessage(content='Debug this')])
```

### batch() and abatch()

Run multiple independent `invoke()` calls concurrently and return responses in
the same order as the input list. Each input item is passed as the first
argument to `invoke()`, so for `Agent` each item is normally a
`Sequence[BaseMessage]`.

```python
def batch(
    inputs: Iterable[Sequence[BaseMessage]],
    *,
    max_concurrency: int | None = None,
    **invoke_kwargs: Any,
) -> list[SessionResponse]

async def abatch(
    inputs: Iterable[Sequence[BaseMessage]],
    *,
    max_concurrency: int | None = None,
    **invoke_kwargs: Any,
) -> list[SessionResponse]
```

Use `max_concurrency` to cap simultaneous executions. When omitted, the SDK
uses Python's default thread-pool worker count. Any keyword argument accepted
by `invoke()` can be provided once and is shared by every batch item.

```python
from maivn.messages import HumanMessage

batch_inputs = [
    [HumanMessage(content='Summarize ticket A')],
    [HumanMessage(content='Summarize ticket B')],
    [HumanMessage(content='Summarize ticket C')],
]

responses = agent.batch(
    batch_inputs,
    max_concurrency=3,
    force_final_tool=True,
)

for response in responses:
    print(response.result)
```

Async usage:

```python
responses = await agent.abatch(
    batch_inputs,
    max_concurrency=3,
    force_final_tool=True,
)
```

Notes:

- `batch()` and `abatch()` preserve input order even when calls complete out of order.
- `max_concurrency` must be greater than zero.
- Exceptions from any individual invocation are raised to the caller.
- Batch execution is for independent calls. Use a shared `thread_id` only when you
  intentionally want concurrent calls to write to the same conversation thread.

### preview_redaction()

Preview server-side redaction for a `RedactedMessage` without starting an invocation.

```python
def preview_redaction(
    message: RedactedMessage,
    *,
    known_pii_values: list[str] | None = None,
    private_data: dict[str, Any] | None = None,
) -> RedactionPreviewResponse
```

Use this when you want to inspect which placeholders will be inserted, which values will be added to `private_data`, and which caller-supplied literals matched before sending the message to the model. The preview uses the same case-insensitive known-value matching the runtime applies before outbound handoff.

#### Parameters

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `message` | `RedactedMessage` | Required | Candidate message to redact on the server |
| `known_pii_values` | `list[str] \| None` | `None` | Optional literal values that must be redacted if found |
| `private_data` | `dict[str, Any] \| None` | `None` | Optional extra private data merged with the scope's existing `private_data` |

#### Returns

`RedactionPreviewResponse` containing:

- `message`: redacted message with placeholders applied
- `inserted_keys`: newly inserted placeholder keys
- `added_private_data`: only the values added by this preview
- `merged_private_data`: existing plus newly added private data
- `redacted_message_count`
- `redacted_value_count`
- `matched_known_pii_values`
- `unmatched_known_pii_values`

#### Example

```python
from maivn import Agent, RedactedMessage

agent = Agent(name='support', api_key='...')

preview = agent.preview_redaction(
    RedactedMessage(content='Contact me at alice@example.com'),
    known_pii_values=['alice@example.com', 'bob@example.com'],
)

assert preview.inserted_keys == ['pii_email_1']
assert preview.matched_known_pii_values == ['alice@example.com']
```

### stream()

Stream raw SSE events from the server while executing the agent.

```python
def stream(
    messages: Sequence[BaseMessage],
    force_final_tool: bool = False,
    targeted_tools: list[str] | None = None,
    model: Literal['fast', 'balanced', 'max'] | None = None,
    reasoning: Literal['minimal', 'low', 'medium', 'high'] | None = None,
    thread_id: str | None = None,
    verbose: bool = False,
    metadata: dict[str, Any] | None = None,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
    allow_private_in_system_tools: bool | None = None,
) -> Iterator[SSEEvent]
```

The iterator yields each SSE event as it arrives, including:

- tool execution events
- enrichment events (phase changes like `evaluating`, `planning`, `synthesizing`)
- update events (for example `streaming_content` deltas while assistant text is being generated)
- system tool chunk events (`system_tool_chunk`)
- the terminal `final` event containing the final payload

For product integrations, treat `stream()` as the raw transport layer. If you are forwarding execution state to your own frontend, normalize the raw events first via `maivn.events.normalize_stream(...)`.

Example:

```python
for event in agent.stream([HumanMessage(content='Think through this problem')]):
    if event.name == "system_tool_chunk":
        print(event.payload.get("text", ""), end="")
    elif event.name == "final":
        print("\nDone")
```

Recommended app-facing pattern:

```python
from maivn.events import normalize_stream

raw_events = agent.stream(
    [HumanMessage(content="Think through this problem")],
    status_messages=True,
)

for event in normalize_stream(raw_events, default_agent_name="assistant"):
    send_to_frontend(event.model_dump())
```

### events()

Builder method for inline event filtering and payload routing with `.invoke()` or `.stream()`.

```python
def events(
    *,
    include: Sequence[str] | str | None = None,
    exclude: Sequence[str] | str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    auto_verbose: bool = True,
) -> EventInvocationBuilder
```

Supported category tokens:

- `all`
- `tools` or `tool`
- `func`, `model`, `mcp`, `agent`, `system`
- `enrichment`
- `response` (assistant streaming chunks)
- `assignment`
- `lifecycle` (headers, summaries, final response/result, errors)

Default behavior:

- `auto_verbose=True` marks invocations verbose unless you explicitly pass `verbose=...`.
- All event categories are included unless `include` or `exclude` is set.

Examples:

```python
# Default: verbose + all events (including enrichment)
response = agent.events().invoke([HumanMessage(content="Analyze this request")])

# Only enrichment + model events, and route payloads to a callback
def forward(payload: dict[str, Any]) -> None:
    send_to_frontend(payload)

response = agent.events(
    include=["enrichment", "model"],
    on_event=forward,
).invoke([HumanMessage(content="Summarize this")])

# Stream with tool filtering
for event in agent.events(exclude=["system"]).stream(
    [HumanMessage(content="Research this topic")]
):
    if event.name == "final":
        break
```

### toolify()

Decorator to register a function or Pydantic model as a tool.
For non-decorator registration, use `add_tool(...)` or `Agent(..., tools=[...])`.

```python
def toolify(
    name: str | None = None,
    description: str | None = None,
    *,
    always_execute: bool = False,
    final_tool: bool = False,
    tags: list[str] | None = None,
    before_execute: Callable | None = None,
    after_execute: Callable | None = None,
) -> ToolifyDecoratorBuilder
```

#### Parameters

| Parameter        | Type                | Default       | Description                   |
| ---------------- | ------------------- | ------------- | ----------------------------- |
| `name`           | `str \| None`       | Function name | Override tool name            |
| `description`    | `str \| None`       | Docstring     | Tool description for LLM      |
| `always_execute` | `bool`              | `False`       | Always execute this tool      |
| `final_tool`     | `bool`              | `False`       | Mark as the final output tool |
| `tags`           | `list[str] \| None` | `None`        | Tags for organization         |
| `before_execute` | `Callable \| None`  | `None`        | Hook before tool execution    |
| `after_execute`  | `Callable \| None`  | `None`        | Hook after tool execution     |

#### Examples

```python
# Function tool
@agent.toolify(description='Get weather for a city')
def get_weather(city: str) -> dict:
    return {'city': city, 'temp': 72}

# Model tool (structured output)
@agent.toolify(final_tool=True)
class WeatherReport(BaseModel):
    city: str
    temperature: int
    summary: str

# With hooks
@agent.toolify(
    description='Process data',
    before_execute=lambda ctx: print('Starting...'),
    after_execute=lambda ctx: print('Done!'),
)
def process_data(data: dict) -> dict:
    return {'processed': True}
```

### compile_state()

Compile agent state into a session request without executing.

```python
def compile_state(
    messages: Sequence[BaseMessage],
    targeted_tools: list[str] | None = None,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
) -> SessionRequest
```

Useful for debugging or inspecting what would be sent to the server.

### list_tools()

Get all registered tools.

```python
def list_tools() -> list[BaseTool]
```

### close()

Release resources (MCP servers, orchestrator).

```python
def close() -> None
```

Call when done with the agent to clean up.

### structured_output()

Builder method for fast structured output. **Bypasses the orchestrator** for direct LLM-to-schema execution.
This builder is **invoke-only** (`.structured_output(...).invoke(...)`) and is not used by `.stream()`.

```python
def structured_output(model: type[BaseModel]) -> StructuredOutputInvocationBuilder
```

#### Parameters

| Parameter | Type              | Description                               |
| --------- | ----------------- | ----------------------------------------- |
| `model`   | `type[BaseModel]` | Pydantic model defining the output schema |

#### Returns

`StructuredOutputInvocationBuilder` with an `invoke()` method that accepts:

| Parameter   | Type                                          | Default  | Description                                                  |
| ----------- | --------------------------------------------- | -------- | ------------------------------------------------------------ |
| `messages`  | `Sequence[BaseMessage]`                       | Required | Messages to send                                             |
| `model`     | `Literal['fast', 'balanced', 'max']`          | `None`   | LLM selection hint                                           |
| `reasoning` | `Literal['minimal', 'low', 'medium', 'high']` | `None`   | Reasoning level                                              |
| `thread_id` | `str \| None`                                 | `None`   | Thread ID for conversations                                  |
| `verbose`   | `bool`                                        | `False`  | Legacy terminal tracing flag. Prefer `events().invoke(...)`. |

#### Why Use This?

The `.structured_output()` builder:

- **Bypasses orchestration**: Routes directly to the assignment agent, skipping higher-level planning
- **Faster responses**: Reduced latency by skipping orchestrator overhead
- **Tools still execute**: Registered tools are available and will execute as needed

Use this when you need structured output with faster response times by skipping the orchestration layer.

#### Example

```python
from pydantic import BaseModel, Field
from maivn import Agent
from maivn.messages import HumanMessage

class SentimentAnalysis(BaseModel):
    sentiment: str = Field(..., description='positive, negative, or neutral')
    confidence: float = Field(..., ge=0, le=1)
    reasoning: str

agent = Agent(name='analyzer', api_key='...')

# Fast structured output - bypasses orchestrator
response = agent.structured_output(SentimentAnalysis).invoke(
    [HumanMessage(content='Analyze: "I love this product!"')]
)
```

#### Comparison with final_tool Pattern

| Aspect         | `.structured_output()`                 | `final_tool` + `force_final_tool` |
| -------------- | -------------------------------------- | --------------------------------- |
| Orchestration  | **Bypassed** (direct to assignment)    | Full orchestration                |
| Tool execution | Tools execute as needed                | Tools execute as needed           |
| Speed          | Faster (skips orchestrator)            | Standard                          |
| Use case       | When orchestration overhead not needed | Complex multi-step workflows      |
| Swarm support  | **Not supported**                      | Supported                         |

See the [Structured Output Guide](../guides/structured-output.md) for detailed patterns.

## Properties

### agent_id

Unique identifier for this agent.

```python
@property
def agent_id(self) -> str
```

### private_data

Server-side secret data dictionary.

```python
agent.private_data = {'api_key': 'secret'}
value = agent.private_data.get('api_key')
```

## Execution Hooks

Hooks allow custom logic before/after tool execution.

### Hook Payload

```python
{
    'stage': 'before' | 'after',
    'tool_id': str | None,
    'tool': BaseTool | None,
    'args': dict | None,
    'context': ExecutionContext,
    'result': Any | None,  # Only in 'after' stage
    'error': Exception | None,  # Only if error occurred
}
```

### Hook Execution Modes

| Mode      | Description                          |
| --------- | ------------------------------------ |
| `'tool'`  | Hooks fire per-tool (default)        |
| `'scope'` | Hooks fire once per agent invocation |
| `'agent'` | Alias for `'scope'`                  |

### Example

```python
def log_execution(payload):
    if payload['stage'] == 'before':
        print(f"Starting: {payload['tool_id']}")
    else:
        print(f"Finished: {payload['tool_id']}")

agent = Agent(
    name='my_agent',
    api_key='...',
    before_execute=log_execution,
    after_execute=log_execution,
)
```

## Context Manager

Agent supports context manager protocol for resource cleanup:

```python
with Agent(name='temp', api_key='...') as agent:
    @agent.toolify()
    def my_tool() -> dict:
        return {}
    response = agent.invoke([...])
# Resources automatically cleaned up
```

## BaseScope

`Agent` inherits from `BaseScope`, which provides:

- Tool registration (`toolify()`, `add_tool(...)`, and constructor `tools=[...]`)
- Tool compilation (`compile_tools()`)
- Tool validation (`validate_tool_configuration()`)
- MCP server registration (`register_mcp_servers()`)
- Batch invocation (`batch()` and `abatch()`)

Both `Agent` and `Swarm` inherit these capabilities.

## See Also

- [Swarm](swarm.md) - Multi-agent orchestration
- [Decorators](decorators.md) - Dependency decorators
- [Tools Guide](../guides/tools.md) - Tool definition patterns
- [Structured Output Guide](../guides/structured-output.md) - Final tool pattern
````
