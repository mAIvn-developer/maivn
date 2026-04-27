# Swarm

The `Swarm` class coordinates multiple agents for complex multi-agent workflows. It provides shared tool access and automatic agent-to-agent communication.

## Import

```python
from maivn import Swarm
```

## Constructor

```python
Swarm(
    name: str | None = None,
    description: str | None = None,
    system_prompt: str | SystemMessage | None = None,
    agents: list[Agent] = [],
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

| Parameter             | Type                             | Default    | Description                                                          |
| --------------------- | -------------------------------- | ---------- | -------------------------------------------------------------------- |
| `name`                | `str \| None`                    | Class name | Swarm name for identification                                        |
| `description`         | `str \| None`                    | `None`     | Human-readable swarm description                                     |
| `system_prompt`       | `str \| SystemMessage \| None`   | `None`     | System message for swarm context                                     |
| `agents`              | `list[Agent]`                    | `[]`       | Initial list of agents                                               |
| `private_data`        | `dict`                           | `{}`       | Swarm-level private data                                             |
| `memory_config`       | `MemoryConfig \| dict[str, Any]` | `{}`       | Default typed memory configuration applied on every swarm invocation |
| `skills`              | `list[dict[str, Any]]`           | `[]`       | Optional user-defined memory skill payloads for swarm context        |
| `resources`           | `list[dict[str, Any]]`           | `[]`       | Optional bound resource payloads for swarm context                   |
| `tags`                | `list[str]`                      | `[]`       | Tags for organization                                                |
| `before_execute`      | `Callable \| None`               | `None`     | Hook before execution                                                |
| `after_execute`       | `Callable \| None`               | `None`     | Hook after execution                                                 |
| `hook_execution_mode` | `Literal`                        | `'tool'`   | When hooks fire                                                      |

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

`skills` and `resources` on a swarm are normalized by the SDK and merged into metadata as:

- `memory_defined_skills`
- `memory_bound_resources`

Per-agent `skills`/`resources` are also surfaced in swarm roster metadata.

Legacy note: `documents` remains accepted as a compatibility alias for older SDK code.

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
    thread_id: str | None = None,
    verbose: bool = False,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
    allow_private_in_system_tools: bool | None = None,
) -> SessionResponse
```

#### Parameters

| Parameter                       | Type                                     | Default  | Description                                                                            |
| ------------------------------- | ---------------------------------------- | -------- | -------------------------------------------------------------------------------------- |
| `messages`                      | `Sequence[BaseMessage] \| BaseMessage`   | Required | Messages to send                                                                       |
| `model`                         | `Any`                                    | `None`   | LLM selection hint                                                                     |
| `reasoning`                     | `Any`                                    | `None`   | Reasoning level                                                                        |
| `force_final_tool`              | `bool`                                   | `False`  | Force final tool output                                                                |
| `thread_id`                     | `str \| None`                            | `None`   | Thread ID for continuity                                                               |
| `verbose`                       | `bool`                                   | `False`  | Legacy terminal tracing flag. Prefer `events().invoke(...)`.                           |
| `memory_config`                 | `MemoryConfig \| dict[str, Any] \| None` | `None`   | Per-invocation memory override merged over swarm defaults                              |
| `allow_private_in_system_tools` | `bool \| None`                           | `None`   | Optional override to allow raw private data access in system tools (advanced use only) |

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

### preview_redaction()

Preview server-side redaction for a `RedactedMessage` without starting a swarm invocation.

```python
def preview_redaction(
    message: RedactedMessage,
    *,
    known_pii_values: list[str] | None = None,
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
    thread_id: str | None = None,
    verbose: bool = False,
    memory_config: MemoryConfig | dict[str, Any] | None = None,
    allow_private_in_system_tools: bool | None = None,
) -> Iterator[SSEEvent]
```

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
| Use case           | Single-agent tasks       | Multi-agent coordination |

## See Also

- [Agent](agent.md) - Individual agent reference
- [Decorators](decorators.md) - `@depends_on_agent` decorator
- [Multi-Agent Guide](../guides/multi-agent.md) - Detailed patterns
