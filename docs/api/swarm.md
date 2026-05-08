# Swarm

The `Swarm` class coordinates multiple agents for complex multi-agent workflows. It provides shared tool access and automatic agent-to-agent communication.

## Import

```python
from maivn import (
    MemoryConfig,
    SessionOrchestrationConfig,
    Swarm,
    SystemToolsConfig,
)
```

## Constructor

```python
Swarm(
    name: str | None = None,
    description: str | None = None,
    system_prompt: str | SystemMessage | None = None,
    agents: list[Agent] = [],
    private_data: dict | list[PrivateData] = {},
    allow_private_in_system_tools: bool = False,
    memory_config: MemoryConfig | dict[str, Any] = {},
    system_tools_config: SystemToolsConfig | dict[str, Any] = {},
    orchestration_config: SessionOrchestrationConfig | dict[str, Any] = {},
    skills: list[dict[str, Any]] = [],
    resources: list[dict[str, Any]] = [],
    tags: list[str] = [],
    before_execute: Callable | None = None,
    after_execute: Callable | None = None,
    hook_execution_mode: Literal['tool', 'scope', 'agent'] = 'tool',
)
```

> All parameters are keyword-only at the call site (Pydantic model fields).

### Parameters

| Parameter                       | Type                                 | Default  | Description                                                                                                                    |
| ------------------------------- | ------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `name`                          | `str \| None`                        | `None`   | Swarm name for identification (`id` falls back to class name when unset)                                                       |
| `description`                   | `str \| None`                        | `None`   | Human-readable swarm description                                                                                               |
| `system_prompt`                 | `str \| SystemMessage \| None`       | `None`   | System message for swarm context                                                                                               |
| `agents`                        | `list[Agent]`                        | `[]`     | Initial list of agents                                                                                                         |
| `private_data`                  | `dict \| list[PrivateData]`          | `{}`     | Swarm-level private data                                                                                                       |
| `allow_private_in_system_tools` | `bool`                               | `False`  | Permit raw `private_data` values to flow through system tools (web search, repl). Defaults to `False`; opt in only when needed |
| `memory_config`                 | `MemoryConfig \| dict[str, Any]`     | `{}`     | Default typed memory configuration applied on every swarm invocation                                                           |
| `system_tools_config`           | `SystemToolsConfig \| dict`          | `{}`     | Default typed system-tool allowlists and approval controls applied on every swarm invocation                                   |
| `orchestration_config`          | `SessionOrchestrationConfig \| dict` | `{}`     | Default typed orchestration loop controls applied on every swarm invocation                                                    |
| `skills`                        | `list[dict[str, Any]]`               | `[]`     | Optional user-defined memory skill payloads surfaced through `MemoryAssetsConfig`                                              |
| `resources`                     | `list[dict[str, Any]]`               | `[]`     | Optional bound resource payloads surfaced through `MemoryAssetsConfig`                                                         |
| `tags`                          | `list[str]`                          | `[]`     | Tags for organization                                                                                                          |
| `before_execute`                | `Callable \| None`                   | `None`   | Hook before execution                                                                                                          |
| `after_execute`                 | `Callable \| None`                   | `None`   | Hook after execution                                                                                                           |
| `hook_execution_mode`           | `Literal`                            | `'tool'` | When hooks fire                                                                                                                |

### Memory Configuration

`memory_config` stores default typed memory behavior applied on each swarm invocation.

Common fields:

| Key                     | Type                            | Example                       | Purpose                                                      |
| ----------------------- | ------------------------------- | ----------------------------- | ------------------------------------------------------------ |
| `enabled`               | `bool`                          | `True`                        | Master toggle for public memory behavior                     |
| `level`                 | `str`                           | `"focus"`                     | Memory behavior level: `none`, `glimpse`, `focus`, `clarity` |
| `summarization_enabled` | `bool`                          | `True`                        | Optional summarization override                              |
| `persistence_mode`      | `str`                           | `"vector_plus_graph"`         | Optional downscope for persistence writes                    |
| `retrieval`             | `MemoryRetrievalConfig`         | `{"resources_enabled": True}` | Retrieval tuning and signal toggles                          |
| `skill_extraction`      | `MemorySkillExtractionConfig`   | `{"sharing_scope": "swarm"}`  | Skill extraction controls                                    |
| `insight_extraction`    | `MemoryInsightExtractionConfig` | `{"enabled": True}`           | Insight extraction controls                                  |

Notes:

- Effective behavior is policy-gated server-side by workspace and plan limits.
- Per-agent and per-invocation `memory_config` values may further downscope behavior.
- `thread_id` governs episodic recall; scoped skills, resources, and promoted insights are reused across future threads.
- `insight_extraction.sharing_scope` is limited to `agent` or `swarm` for AI-generated insights. Use portal promotion for broader reuse.

### Skills and Resources

`skills` and `resources` on a swarm are normalized by the SDK into `MemoryAssetsConfig`
on the typed request and forwarded to the platform alongside the swarm roster.

Per-agent `skills`/`resources` are also surfaced in the typed swarm roster config.

### Invocation Config Objects

`Swarm` accepts typed config objects at two layers:

- Scope defaults on `Swarm(...)`, merged into every swarm run.
- Per-call overrides on `invoke()`, `stream()`, `ainvoke()`, and `astream()`.

Use these fields for runtime controls. Request `metadata` is reserved for application labels,
correlation IDs, and other user-owned data.

| Component              | Constructor field      | Invocation field       | Reference                                                                    |
| ---------------------- | ---------------------- | ---------------------- | ---------------------------------------------------------------------------- |
| Memory behavior        | `memory_config`        | `memory_config`        | [`MemoryConfig`](session-config.md#memoryconfig)                             |
| System tools           | `system_tools_config`  | `system_tools_config`  | [`SystemToolsConfig`](session-config.md#systemtoolsconfig)                   |
| Orchestration loop     | `orchestration_config` | `orchestration_config` | [`SessionOrchestrationConfig`](session-config.md#sessionorchestrationconfig) |
| Memory assets          | `skills`, `resources`  | n/a                    | [`MemoryAssetsConfig`](session-config.md#memoryassetsconfig)                 |
| Swarm roster transport | generated by SDK       | generated by SDK       | [`SwarmConfig`](session-config.md#swarmconfig)                               |

### Final Output Agents and Supervision Policy

`use_as_final_output=True` identifies the swarm member whose output can become the
user-facing response. It does not have to mean the run is complete. Completion behavior
is controlled by `SessionOrchestrationConfig`:

```python
from maivn import SessionOrchestrationConfig, Swarm

swarm = Swarm(
    name="repair_swarm",
    agents=[verifier, editor, director],
    orchestration_config=SessionOrchestrationConfig(
        mode="supervisor_loop",
        final_output_mode="supervised",
        allow_followup_actions=True,
        stop_strategy="objective_satisfied",
        max_cycles=5,
    ),
)
```

Use `final_output_mode="terminal"` for reporting-only swarms where the final-output
agent should end the run immediately. Use `final_output_mode="supervised"` when the
orchestrator should inspect results from the final-output agent and decide whether more
actions are required. Repair, validation, and multi-pass cleanup workflows should prefer
`mode="supervisor_loop"` plus `stop_strategy="objective_satisfied"`.

Supervised Swarms may deploy the same member agent more than once. Each deployment is
a distinct invocation with its own server-minted action ID, even when the display name
is the same. If your frontend groups events, key repeated member cards by invocation
ID or assignment/action ID rather than by agent name.

If a member agent must return a typed report every time it is deployed, set
`Agent(..., force_final_tool=True)` on that member and register the report model as
`final_tool=True` (optionally also `always_execute=True`). That per-agent flag is
honored for nested Swarm invocations.

## Methods

### invoke()

Invoke the swarm with messages.

```python
def invoke(
    messages: Sequence[BaseMessage] | BaseMessage,
    *,
    model: Any = None,
    reasoning: Any = None,
    force_final_tool: bool = False,
    stream_response: bool = True,
    thread_id: str | None = None,
    verbose: bool = False,
    metadata: dict[str, Any] | None = None,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
    system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
    orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
    allow_private_in_system_tools: bool | None = None,
) -> SessionResponse
```

#### Parameters

| Parameter                       | Type                                         | Default  | Description                                                                            |
| ------------------------------- | -------------------------------------------- | -------- | -------------------------------------------------------------------------------------- |
| `messages`                      | `Sequence[BaseMessage] \| BaseMessage`       | Required | Messages to send                                                                       |
| `model`                         | `Any`                                        | `None`   | LLM selection hint                                                                     |
| `reasoning`                     | `Any`                                        | `None`   | Reasoning level                                                                        |
| `force_final_tool`              | `bool`                                       | `False`  | Force final tool output                                                                |
| `stream_response`               | `bool`                                       | `True`   | Request streamed model output from the server transport                                |
| `thread_id`                     | `str \| None`                                | `None`   | Thread ID for continuity                                                               |
| `verbose`                       | `bool`                                       | `False`  | Legacy terminal tracing flag. Prefer `events().invoke(...)`.                           |
| `metadata`                      | `dict \| None`                               | `None`   | Application metadata only. Reserved runtime-control keys are rejected.                 |
| `memory_config`                 | `MemoryConfig \| dict[str, Any] \| None`     | `None`   | Per-invocation memory override merged over swarm defaults                              |
| `system_tools_config`           | `SystemToolsConfig \| dict \| None`          | `None`   | Per-invocation system-tool allowlist and approval controls                             |
| `orchestration_config`          | `SessionOrchestrationConfig \| dict \| None` | `None`   | Per-invocation orchestration loop controls such as reevaluate-loop and cycle limits    |
| `allow_private_in_system_tools` | `bool \| None`                               | `None`   | Optional override to allow raw private data access in system tools (advanced use only) |

#### Returns

`SessionResponse` with the swarm's coordinated response.

#### Raises

- `ValueError`: If swarm has no agents
- `ValueError`: If `force_final_tool=True` but no final tool or final output agent defined

#### Example

```python
from maivn.messages import HumanMessage

response = swarm.events().invoke(
    HumanMessage(content='Research and write about AI'),
    force_final_tool=True,
)
```

### ainvoke()

Async wrapper around `invoke()`. Same parameters and return value;
suitable for awaiting inside an event loop.

```python
response = await swarm.ainvoke(HumanMessage(content='Plan the launch'))
```

### batch() and abatch()

Run multiple independent `Swarm.invoke()` calls concurrently and return
responses in the same order as the input list. Each input item is passed as the
first argument to `Swarm.invoke()`, so each item may be a single `BaseMessage`
or a sequence of messages.

```python
def batch(
    inputs: Iterable[Sequence[BaseMessage] | BaseMessage],
    *,
    max_concurrency: int | None = None,
    **invoke_kwargs: Any,
) -> list[SessionResponse]

async def abatch(
    inputs: Iterable[Sequence[BaseMessage] | BaseMessage],
    *,
    max_concurrency: int | None = None,
    **invoke_kwargs: Any,
) -> list[SessionResponse]
```

Use `max_concurrency` to cap simultaneous swarm executions. Any keyword
argument accepted by `Swarm.invoke()` can be provided once and is shared by
every batch item.

```python
from maivn.messages import HumanMessage

requests = [
    HumanMessage(content='Research topic A and write the brief'),
    HumanMessage(content='Research topic B and write the brief'),
    HumanMessage(content='Research topic C and write the brief'),
]

responses = swarm.batch(
    requests,
    max_concurrency=2,
    force_final_tool=True,
)
```

Async usage:

```python
responses = await swarm.abatch(
    requests,
    max_concurrency=2,
    force_final_tool=True,
)
```

Notes:

- `batch()` and `abatch()` preserve input order even when calls complete out of order.
- `max_concurrency` must be greater than zero.
- Exceptions from any individual swarm invocation are raised to the caller.
- Batch execution is for independent calls. Use a shared `thread_id` only when you
  intentionally want concurrent calls to write to the same conversation thread.

### preview_redaction()

Preview server-side redaction for a `RedactedMessage` without starting a swarm invocation.

```python
def preview_redaction(
    message: RedactedMessage,
    *,
    known_pii_values: list[str | PrivateData] | None = None,
    private_data: dict[str, Any] | None = None,
) -> RedactionPreviewResponse
```

`Swarm.preview_redaction()` uses the swarm's configured client or its entry agent's client, merges swarm-level `private_data` with any call-specific `private_data`, and returns the full preview report before execution begins.

#### Example

```python
from maivn import Agent, RedactedMessage, Swarm

researcher = Agent(name='researcher', api_key='...')
swarm = Swarm(name='research_swarm', agents=[researcher])

preview = swarm.preview_redaction(
    RedactedMessage(content='Reach me at alice@example.com'),
    known_pii_values=['alice@example.com'],
)

assert preview.redacted_value_count == 1
```

### stream()

Stream raw SSE events while the swarm executes.

```python
def stream(
    messages: Sequence[BaseMessage] | BaseMessage,
    *,
    model: Any = None,
    reasoning: Any = None,
    force_final_tool: bool = False,
    stream_response: bool = True,
    status_messages: bool = False,
    thread_id: str | None = None,
    verbose: bool = False,
    metadata: dict[str, Any] | None = None,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
    system_tools_config: SystemToolsConfig | dict[str, Any] | None = None,
    orchestration_config: SessionOrchestrationConfig | dict[str, Any] | None = None,
    allow_private_in_system_tools: bool | None = None,
) -> Iterator[SSEEvent]
```

#### Parameters

| Parameter                       | Type                                                   | Default  | Description                                                              |
| ------------------------------- | ------------------------------------------------------ | -------- | ------------------------------------------------------------------------ |
| `messages`                      | `Sequence[BaseMessage] \| BaseMessage`                 | Required | Messages to send to the swarm                                            |
| `model`                         | `Any`                                                  | `None`   | LLM selection hint                                                       |
| `reasoning`                     | `Any`                                                  | `None`   | Reasoning level                                                          |
| `force_final_tool`              | `bool`                                                 | `False`  | Force the final tool or final-output agent result                        |
| `stream_response`               | `bool`                                                 | `True`   | Request streamed model output from the server transport                  |
| `status_messages`               | `bool`                                                 | `False`  | Opt into normalized status-message events for frontend progress displays |
| `thread_id`                     | `str \| None`                                          | `None`   | Thread ID for continuity                                                 |
| `verbose`                       | `bool`                                                 | `False`  | Legacy terminal tracing flag. Prefer `events().stream(...)`.             |
| `metadata`                      | `dict[str, Any] \| None`                               | `None`   | Application metadata only. Reserved runtime-control keys are rejected.   |
| `memory_config`                 | `MemoryConfig \| dict[str, Any] \| None`               | `None`   | Per-invocation memory override merged over swarm defaults                |
| `system_tools_config`           | `SystemToolsConfig \| dict[str, Any] \| None`          | `None`   | Per-invocation system-tool allowlist and approval controls               |
| `orchestration_config`          | `SessionOrchestrationConfig \| dict[str, Any] \| None` | `None`   | Per-invocation orchestration loop controls                               |
| `allow_private_in_system_tools` | `bool \| None`                                         | `None`   | Optional override to allow raw private data access in system tools       |

The iterator yields every server event as it arrives, including:

- `update` events with streamed `streaming_content`
- `system_tool_chunk` events
- the terminal `final` event payload

For application integrations, prefer normalizing these raw events before forwarding them to a frontend or external transport.

```python
from maivn.events import normalize_stream

for event in normalize_stream(
    swarm.stream(HumanMessage(content="Run the multi-agent workflow"), status_messages=True),
    default_swarm_name="research_swarm",
):
    post_to_ui(event.model_dump())
```

### astream()

Async wrapper around `stream()`. Yields `SSEEvent` instances back into
the caller's event loop:

```python
async for event in swarm.astream(HumanMessage(content='Run the workflow')):
    handle(event)
```

### cron(), every(), at()

Build a chainable schedule that runs the swarm on a cadence. Inherited
from `BaseScope`, identical surface to `Agent.cron(...)`.

```python
from datetime import timedelta
from maivn import JitterSpec

job = swarm.cron(
    '*/15 * * * *',
    tz='UTC',
    jitter=JitterSpec(min=timedelta(0), max=timedelta(seconds=90)),
    overlap_policy='skip',     # never let a fire collide with a slow run
    max_overlap=1,
).invoke(HumanMessage(content='Run the briefing pipeline'))

job.on_success(lambda r: log.info('briefing succeeded in %s', r.duration))
```

`every(interval, ...)` and `at(when, ...)` are also available. Each
returns a `CronInvocationBuilder` whose terminal methods (`invoke`,
`stream`, `batch`, `ainvoke`, `astream`, `abatch`) start the job and
return a `ScheduledJob`.

See the [Scheduling reference](scheduling.md) for the full builder,
jitter, retry, and lifecycle surface, and the
[Scheduled Invocation guide](../guides/scheduled-invocation.md) for
patterns and the production checklist.

### events()

`Swarm` also supports the same `events(...)` builder as `Agent` (inherited from `BaseScope`):

```python
response = swarm.events(
    include=["enrichment", "agent", "model"],
    on_event=lambda payload: post_to_ui(payload),
).invoke(
    HumanMessage(content="Run the multi-agent workflow")
)
```

Use `include` / `exclude` to filter categories (`func`, `model`, `mcp`, `agent`, `system`, `enrichment`, `response`, `assignment`, `lifecycle`) and `on_event` to route payloads externally.

### add_agent()

Add an agent to the swarm.

```python
def add_agent(agent: Agent) -> None
```

The agent becomes a member of the swarm and can be referenced by other agents via `@depends_on_agent`.

### member()

Register swarm member agents declaratively.

```python
@swarm.member
def researcher() -> Agent:
    return Agent(name='researcher', api_key='...')
```

`member` accepts either a zero-argument `Agent` factory or an existing `Agent` instance. It
returns the registered `Agent`.

The builder form attaches dependencies to the generated agent invocation tool:

```python
@swarm.toolify(description='Load launch context')
def load_context() -> dict:
    return {'market': 'healthcare'}

@swarm.member
@depends_on_tool(load_context, arg_name='context')
def researcher() -> Agent:
    return Agent(name='researcher', api_key='...')

writer = swarm.member.depends_on_agent(
    researcher,
    arg_name='research_notes',
)(Agent(name='writer', api_key='...', use_as_final_output=True))
```

Supported member-builder dependencies:

- `swarm.member.depends_on_tool(...)`
- `swarm.member.depends_on_agent(...)`
- `swarm.member.depends_on_await_for(...)`
- `swarm.member.depends_on_reevaluate(...)`
- `swarm.member.depends_on_interrupt(...)`

Private-data dependencies are not supported directly on member agents. Put private-data access
behind a swarm-level tool and make the member agent depend on that tool.

### get_agent()

Retrieve an agent by ID.

```python
def get_agent(agent_id: str) -> Agent | None
```

### list_agents()

List all agents in the swarm.

```python
def list_agents() -> list[Agent]
```

### toolify()

Register swarm-level tools (available to all agents).

```python
@swarm.toolify(description='Shared utility')
def shared_tool(data: dict) -> dict:
    return {'processed': True}
```

Swarm-level tools are accessible by all member agents.

## Properties

### member_agent_repository

Access the internal agent repository.

```python
@property
def member_agent_repository(self) -> AgentRepoInterface
```

### member_tool_repository

Access the internal tool repository.

```python
@property
def member_tool_repository(self) -> ToolRepoInterface
```

## Swarm Patterns

### Basic Multi-Agent

```python
from maivn import Agent, Swarm, depends_on_agent

# Create specialized agents
researcher = Agent(
    name='researcher',
    description='Researches topics thoroughly',
    system_prompt='You are a research specialist.',
    api_key='...',
)

analyst = Agent(
    name='analyst',
    description='Analyzes research findings',
    system_prompt='You analyze data and provide insights.',
    api_key='...',
)

writer = Agent(
    name='writer',
    description='Writes clear reports',
    system_prompt='You write professional reports.',
    api_key='...',
    use_as_final_output=True,  # This agent produces the final output
)

# Define tools with agent dependencies
@researcher.toolify(description='Research a topic')
def research(topic: str) -> dict:
    return {'findings': f'Research on {topic}'}

@analyst.toolify(description='Analyze research')
@depends_on_agent(researcher, arg_name='research_data')
def analyze(research_data: dict) -> dict:
    return {'analysis': f'Analysis of {research_data}'}

@writer.toolify(description='Write report')
@depends_on_agent(analyst, arg_name='analysis')
def write_report(analysis: dict) -> dict:
    return {'report': f'Report based on {analysis}'}

# Create swarm
swarm = Swarm(
    name='research_team',
    description='Team that researches, analyzes, and reports',
    agents=[researcher, analyst, writer],
)

# Invoke
response = swarm.invoke(
    HumanMessage(content='Research AI trends'),
    force_final_tool=True,
)
```

### Declarative Member Registration

Use `swarm.member` when you want the swarm to own agent registration and dependency metadata
near the agent definition:

```python
swarm = Swarm(name='research_team')

@swarm.member
def researcher() -> Agent:
    return Agent(name='researcher', api_key='...')

writer = swarm.member.depends_on_agent(
    researcher,
    arg_name='research',
)(Agent(name='writer', api_key='...', use_as_final_output=True))
```

### Parallel Agent Execution

When agents don't depend on each other, they can execute in parallel:

```python
data_agent = Agent(name='data', ...)
viz_agent = Agent(name='visualization', ...)

@data_agent.toolify()
def fetch_data() -> dict:
    return {'data': [...]}

@viz_agent.toolify()
def create_charts() -> dict:
    return {'charts': [...]}

# Final agent depends on both
@final_agent.toolify(final_tool=True)
@depends_on_agent(data_agent, arg_name='data')
@depends_on_agent(viz_agent, arg_name='charts')
class Report(BaseModel):
    data_summary: str
    chart_descriptions: list[str]
```

### use_as_final_output

Mark one agent to produce the final output:

```python
writer = Agent(
    name='writer',
    api_key='...',
    use_as_final_output=True,  # Only ONE agent can have this
)
```

When `swarm.invoke(force_final_tool=True)` is called, the swarm coordinates to ensure this agent's output is returned.

## Validation

### Tool Configuration

Swarm validates per-scope, not swarm-wide:

- Each agent may have at most ONE tool with `final_tool=True` (that agent's scope).
- The swarm itself may have at most ONE swarm-scope tool with `final_tool=True`.
- When two or more scopes in the swarm declare a `final_tool` (multiple agents,
  or an agent plus swarm-scope), exactly ONE agent must be marked
  `use_as_final_output=True` so the swarm's final response is unambiguous.
- At most ONE agent across the swarm may be marked `use_as_final_output=True`.
- `always_execute` and `final_tool` may coexist — on the same tool or on
  different tools in the same scope. The orchestrator composes them.

### Example Errors

Two tools in the same scope marked `final_tool=True`:

```
TOOL CONFIGURATION ERROR
================================================================================
[ERROR] Multiple tools marked with final_tool=True: 'Report', 'Summary'
  SCOPE: Agent 'writer' within Swarm 'my_swarm'
  ISSUE: Only ONE tool per scope can be designated as the final output tool.
  FIX: Remove 'final_tool=True' from all but one tool in this scope.
================================================================================
```

Multiple agents own a `final_tool` but no designated final-output agent:

```
TOOL CONFIGURATION ERROR
================================================================================
[ERROR] Ambiguous final_tool ownership in Swarm 'my_swarm'
  Final tools declared on: agents: 'analyst', 'writer'
  ISSUE: When multiple scopes in a swarm declare final_tool, the swarm's
         final response agent must be designated explicitly.
  FIX: Set use_as_final_output=True on exactly one agent.
================================================================================
```

## Swarm vs Agent Invocation

| Feature            | `agent.invoke()`         | `swarm.invoke()`         |
| ------------------ | ------------------------ | ------------------------ |
| Scope              | Single agent + its tools | All agents + all tools   |
| Agent dependencies | Resolved within swarm    | Fully coordinated        |
| Final output       | Agent's `final_tool`     | Swarm's final agent/tool |
| Batch execution    | `agent.batch()`          | `swarm.batch()`          |
| Use case           | Single-agent tasks       | Multi-agent coordination |

## See Also

- [Agent](agent.md) - Individual agent reference
- [Decorators](decorators.md) - `@depends_on_agent` decorator
- [Multi-Agent Guide](../guides/multi-agent.md) - Detailed patterns
