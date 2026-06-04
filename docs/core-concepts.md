# Core Concepts

The vocabulary you need before the deep guides. Each concept below opens with a
plain-language definition, then shows exactly which part of the SDK embodies it —
a class, method, or decorator — with a small snippet and a link to the full guide.

If you have not run an agent yet, start with
[Getting Started](guides/getting-started.md) and come back here when you want the
mental model. This page is the hub the Quickstart hands off to; it is deliberately
scannable rather than exhaustive.

## Scopes: Agent and Swarm

**In plain terms:** a *scope* is the unit you hand work to. It owns a set of tools,
some configuration, and the credentials to run. You build either a single worker
(an `Agent`) or a coordinated team (a `Swarm`) — and because both are scopes, they
behave the same way at the edges.

`Agent` and `Swarm` both subclass `BaseScope`, so they share the same surface:

- **Tool registration** — `toolify(...)`, `add_tool(...)`, `add_toolset(...)`
- **Invocation** — `invoke()`, `stream()`, and the async `ainvoke()` / `astream()`
- **Structured output** — `structured_output(MyModel)`
- **Concurrency** — `batch(...)` / `abatch(...)` for many independent calls at once
- **Scheduling** — `cron(...)`, `every(...)`, `at(...)`
- **Events** — `events(...)` for filtered progress reporting

```python
from maivn import Agent, Swarm

agent = Agent(name='assistant', api_key='...')
swarm = Swarm(name='team', agents=[agent])

# The same call shape works on either scope.
agent.invoke([...])
swarm.invoke([...])
```

Because the interface is shared, almost everything you learn about an `Agent`
transfers to a `Swarm`. The differences are about *coordination* (covered below),
not about how you call them.

> **Note:** `BaseScope` is the shared base, not something you usually instantiate
> directly. You build `Agent`s and `Swarm`s.

## Agents

**In plain terms:** an agent is a single worker with a goal, a set of tools it may
call, and the rules (its system prompt) that shape how it behaves. You give it a
message; it decides what to do and answers.

The `Agent` class is the primary entry point. It needs either an `api_key` or a
preconfigured `Client`.

```python
import os
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(
    name='weather_agent',
    description='Provides weather information',
    system_prompt='You are a helpful weather assistant.',
    api_key=os.environ['MAIVN_API_KEY'],
)

response = agent.invoke([HumanMessage(content='What is the weather in Austin?')])
print(response.response)
```

### Running an agent

There are four ways to run, mirroring each other across sync/async:

| Method | Returns | Use when |
| --- | --- | --- |
| `invoke(messages)` | `SessionResponse` | You want the full result in one call |
| `stream(messages)` | `Iterator[SSEEvent]` | You want live events as they happen |
| `ainvoke(messages)` | `SessionResponse` | Same as `invoke`, inside async code |
| `astream(messages)` | `AsyncIterator[SSEEvent]` | Same as `stream`, inside async code |

### Reading the response

`invoke()` returns a `SessionResponse` with two fields you will use constantly:

```python
response = agent.invoke([HumanMessage(content='...')])

response.response   # the final assistant text (a string, or None if there was none)
response.result     # structured / final-tool output (a typed object), if any
```

Use `response.response` for conversational text, and `response.result` when you
asked for [structured output](#structured-output).

→ Full reference: [Agent API](api/agent.md) · how-to: [Getting Started](guides/getting-started.md)

## Tools

**In plain terms:** tools are the things an agent can *do* beyond talking — your own
Python functions, Pydantic models that shape an answer, or prebuilt capabilities.
The agent sees a description of each tool and decides when to call it. Your tool
code runs locally in your environment; only the tool's schema (name, description,
parameters) is shared for orchestration.

There are three registration styles, all equivalent in power:

```python
from maivn import Agent

agent = Agent(name='helper', api_key='...')

# 1. Decorator — define the tool next to the agent.
@agent.toolify(description='Add two numbers together')
def add_numbers(a: int, b: int) -> dict:
    return {'sum': a + b}

# 2. Constructor — pass already-defined callables/models.
agent = Agent(name='helper', api_key='...', tools=[add_numbers])

# 3. Imperative — register after construction, with options.
agent.add_tool(add_numbers, tags=['math'])
```

A tool can be a **function** (it executes logic) or a **Pydantic model** (it shapes
a structured result). Both are registered the same way.

### Toolsets

When several related tools share state — a database handle, an HTTP client, an OAuth
token — group them on a class and register the whole thing with `add_toolset(...)`:

```python
from maivn import toolify, toolset

@toolset(prefix='reports')
class WeeklyReportsToolSet:
    def __init__(self, db_path: str):
        self._db_path = db_path

    @toolify()
    def weekly_signups(self, week_of: str) -> list[dict]:
        """Signup counts by source for the week starting ``week_of``."""
        ...

agent.add_toolset(WeeklyReportsToolSet('/srv/warehouse.db'))
```

Each `@toolify`-marked method becomes a tool with a name prefixed by the toolset
(here `REPORTS_weekly_signups`). `add_toolset` accepts `include` / `exclude` and
`include_tags` / `exclude_tags` filters so one class can be narrowed at
registration.

→ Full reference: [Tools Guide](guides/tools.md)

## Swarms

**In plain terms:** a swarm is a team of agents working toward one outcome. Each
agent keeps its own specialty; the swarm coordinates who does what and who delivers
the final answer.

A `Swarm` holds a list of `Agent`s. The first agent in the list is the **entry
agent** — work starts there. One agent (or the swarm itself) is responsible for the
user-facing answer.

```python
from maivn import Agent, Swarm
from maivn.messages import HumanMessage

researcher = Agent(name='researcher', api_key='...')
writer = Agent(
    name='writer',
    api_key='...',
    use_as_final_output=True,   # this agent produces the final answer
)

swarm = Swarm(name='content_team', agents=[researcher, writer])
response = swarm.invoke(HumanMessage(content='Write about AI agents'))
```

`use_as_final_output=True` marks which agent owns the swarm's response when more
than one agent could produce output. Alternatively, you can define a final tool at
the swarm level — see [structured output](#structured-output) below. A swarm exposes
the same `invoke` / `stream` / async surface as an agent, plus `add_agent`,
`get_agent`, and `list_agents`.

> **Note:** All config keyword arguments are keyword-only on `Swarm`. That is the
> main call-shape difference from `Agent`.

→ Full reference: [Swarm API](api/swarm.md) · how-to: [Multi-Agent Guide](guides/multi-agent.md)

## Dependencies and DAG execution

**In plain terms:** sometimes one piece of work needs another piece's result first.
You declare those relationships with decorators, and the orchestrator figures out
the order — running independent work in parallel and ordered work in sequence.

You do not write a scheduler. You declare *what feeds what*, and the runtime builds
an execution graph from those declarations.

| Decorator | Declares |
| --- | --- |
| `@depends_on_tool(tool, arg_name=...)` | Inject another tool's output as an argument |
| `@depends_on_agent(agent, arg_name=...)` | Inject another agent's output as an argument |
| `@depends_on_await_for(tool, ...)` | Enforce ordering without passing any data |
| `@depends_on_reevaluate(tool, ...)` | Pause planning to inspect results before continuing |

```python
from maivn import depends_on_tool

@agent.toolify(description='Fetch raw data')
def fetch_data(source: str) -> dict:
    return {'records': [1, 2, 3]}

@agent.toolify(description='Process fetched data')
@depends_on_tool(fetch_data, arg_name='raw_data')
def process_data(raw_data: dict) -> dict:
    return {'count': len(raw_data['records'])}
```

When `process_data` is needed, `fetch_data` runs first and its result is injected.

**Conceptually:** these declarations form a directed acyclic graph (DAG). Tools with
no dependency on each other can run at the same time; tools that depend on a result
wait for it. So if `fetch_users` and `fetch_orders` are independent, they run
concurrently; a `build_report` that depends on both waits for both.

`@depends_on_await_for` is for ordering side effects (write an audit entry *after* a
fetch) where no value is passed. `@depends_on_reevaluate` declares a boundary where
planning should stop, look at the actual results, and then plan the next step with
that real context in hand.

→ Full reference: [Dependencies Guide](guides/dependencies.md) · [Decorators API](api/decorators.md)

## Sessions and threads

**In plain terms:** a *session* is one turn of work — you send messages, the agent
runs, you get a response. A *thread* is the string of related turns that should
remember each other.

Each `invoke()` / `stream()` is a session. To make sessions part of a continuing
conversation, pass the same `thread_id`:

```python
agent.invoke([HumanMessage(content='My name is Sam.')], thread_id='chat-42')
agent.invoke([HumanMessage(content='What is my name?')], thread_id='chat-42')
```

Turns that share a `thread_id` carry context forward; turns with different (or no)
`thread_id` are independent.

You can also attach arbitrary `metadata` to a call for your own tracing,
correlation, or labeling:

```python
agent.invoke(
    [HumanMessage(content='Summarize this ticket')],
    thread_id='support-1001',
    metadata={'ticket_id': '1001', 'channel': 'email'},
)
```

→ Continuity across turns is covered in the [Memory and Recall Guide](guides/memory-and-recall.md).

## Structured output

**In plain terms:** instead of free-form text, you can ask the agent for a result
that always matches a shape you define. You get back a typed object you can rely on,
not a string you have to parse.

You define the shape as a Pydantic model. There are two ways to get it back.

**Fast path — `structured_output(...)`.** Best for one-shot extraction,
classification, or summarization. It skips multi-step planning; registered tools
still execute as needed.

```python
from pydantic import BaseModel, Field

class SentimentAnalysis(BaseModel):
    sentiment: str = Field(..., description='positive, negative, or neutral')
    confidence: float = Field(..., ge=0, le=1)

response = agent.structured_output(SentimentAnalysis).invoke(
    [HumanMessage(content='Analyze: "I love this product!"')]
)
typed = response.result   # a SentimentAnalysis instance
```

**Final tool — `final_tool=True` + `force_final_tool`.** Best when tools must run
first and the structured answer comes at the end of a multi-step workflow.

```python
@agent.toolify(final_tool=True)
class Report(BaseModel):
    """A structured report."""
    title: str = Field(..., description='Report title')
    findings: list[str] = Field(..., description='Key findings')

response = agent.invoke(
    [HumanMessage(content='Analyze Q4 sales')],
    force_final_tool=True,
)
print(response.result)
```

Either way, the typed value lands on `response.result`.

→ Full reference: [Structured Output Guide](guides/structured-output.md)

## Headline capabilities at a glance

These are first-class features layered on top of the core model. Each has its own
guide; here is the one-line orientation.

### Private data

Attach sensitive values (API keys, passwords, PII) as `private_data` and inject them
into tools with `@depends_on_private_data`. The model plans against a *schema* — key
names and types only — and never sees raw values by default. Values are supplied
only at tool-execution time, and tool results are scanned and redacted before they
return to the model.

```python
from maivn import depends_on_private_data

@agent.toolify(description='Call external API')
@depends_on_private_data(data_key='api_key', arg_name='secret')
def call_api(query: str, secret: str) -> dict:
    return {'result': f'queried with {query}'}

agent.private_data = {'api_key': 'sk-...'}
```

→ [Private Data Guide](guides/private-data.md)

### Enrichment events

While a turn runs, the SDK streams fixed-label phase indicators (for example
"Evaluating request", "Planning actions", "Synthesizing response") so a UI can show
what is happening between tool calls. Read them via `stream()`, the
`normalize_stream()` / `normalize_stream_event()` helpers, or a custom reporter.

→ [Frontend Events Guide](guides/frontend-events.md) · [Events API](api/events.md)

### Token and usage tracking

Every LLM call's token usage is captured, normalized across providers, and
aggregated for the session. The SDK exposes a `TokenUsage` model (input/output/total,
cache and reasoning tokens, model id, provider, timestamp) on responses, plus public
billing and usage models.

→ [Billing and Usage Guide](guides/billing-and-usage.md)

## How it all fits

A quick conceptual flow, end to end:

1. You build a **scope** — an `Agent` (one worker) or a `Swarm` (a team). Both are
   `BaseScope`, so they share the same call surface.
2. You give the scope **tools** — functions, Pydantic models, toolsets, or prebuilt
   capabilities. Only their schemas are shared for orchestration; your code runs
   locally.
3. You optionally declare **dependencies** between tools and agents. The runtime
   reads those declarations as a DAG and runs independent work in parallel, ordered
   work in sequence.
4. You **invoke** the scope with messages, optionally tagging the call with a
   `thread_id` (for continuity) and `metadata` (for your own tracing).
5. The orchestrator plans, runs tools as needed, and produces a result. You read
   conversational text from `response.response` and **structured output** from
   `response.result`.
6. Throughout, the SDK can stream **enrichment events** for live progress, enforce
   the **private-data** boundary, and record **token usage**.

> **Note:** This describes the SDK contract, not the internal orchestration
> mechanics. You declare intent (tools, dependencies, output shape); the runtime
> decides how to execute it.

## Next steps

- [Getting Started](guides/getting-started.md) — build your first agent end to end
- [Tools Guide](guides/tools.md) — every tool registration pattern in depth
- [Dependencies Guide](guides/dependencies.md) — the full dependency and DAG surface
- [Structured Output Guide](guides/structured-output.md) — guaranteed typed results
- [Multi-Agent Guide](guides/multi-agent.md) — coordinate agents with Swarms
- [Private Data Guide](guides/private-data.md) — the security model for secrets and PII
- [Frontend Events Guide](guides/frontend-events.md) — stream progress to a UI
- [Billing and Usage Guide](guides/billing-and-usage.md) — token and usage models
- [System Tools Guide](guides/system-tools.md) — built-in capabilities provided by the mAIvn service
- [Memory and Recall Guide](guides/memory-and-recall.md) — carry context across turns
- [Scheduled Invocation Guide](guides/scheduled-invocation.md) — run scopes on a schedule
- [API Reference](api/README.md) — the complete public surface
