# Sessions, Invocation, and Streaming

A session is one run of your agent: you give it a message, it works through the
request, and it hands back a result. You can run it and wait for the answer, or
watch it work live as it goes. This guide covers how to start a session, read
what comes back, tune a single run, and run many at once.

## What a session is

Every call to `invoke()`, `stream()`, or one of their async variants starts a
**session** — one turn of work for an `Agent` or `Swarm`. Within that turn the
runtime evaluates your request, runs any tools it needs, and produces a
response. When the turn ends, the session is complete.

Most useful conversations span more than one turn. To keep turns connected,
pass a `thread_id`:

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='assistant', api_key='...')

# First turn
agent.invoke(
    [HumanMessage(content='My name is Dana.')],
    thread_id='conversation-1',
)

# Later turn — same thread, so the agent has the earlier context
agent.invoke(
    [HumanMessage(content='What is my name?')],
    thread_id='conversation-1',
)
```

A `thread_id` is just a string you choose. Reuse it across turns that belong to
the same conversation; use a fresh one to start a clean session. Threads govern
the conversational recall an agent carries from turn to turn — see
[Memory and Recall](memory-and-recall.md) for the cross-thread reuse layer
(skills, bound resources, promoted insights).

> **Note**
> Independent runs should use different threads (or none). A shared
> `thread_id` makes concurrent calls write into the same conversation, which is
> rarely what you want for unrelated work. See [Running many at once](#running-many-at-once).

## invoke vs stream vs ainvoke vs astream

The four entry points differ on two axes: **sync or async**, and **wait for the
result or watch it live**.

| Method     | Sync/Async | Returns                     | Use when                                        |
| ---------- | ---------- | --------------------------- | ----------------------------------------------- |
| `invoke`   | sync       | `SessionResponse`           | You just want the final answer                  |
| `stream`   | sync       | `Iterator[SSEEvent]`        | You want to render progress as it happens       |
| `ainvoke`  | async      | `SessionResponse`           | Same as `invoke`, inside an event loop          |
| `astream`  | async      | `AsyncIterator[SSEEvent]`   | Same as `stream`, inside an event loop          |

All four exist on both `Agent` and `Swarm`, and they accept the same tuning
parameters.

### invoke — run and wait

`invoke()` runs the agent and returns a single `SessionResponse` once the turn
is finished.

```python
response = agent.invoke([HumanMessage(content='What is the weather in Austin?')])
print(response.response)
```

### stream — watch it work live

`stream()` yields events as the turn unfolds: tool execution, phase changes,
assistant text deltas, and a terminal `final` event carrying the completed
payload. Each item is an `SSEEvent` with a `name` and a `payload`.

```python
for event in agent.stream([HumanMessage(content='Think through this problem')]):
    if event.name == 'system_tool_chunk':
        print(event.payload.get('text', ''), end='')
    elif event.name == 'final':
        print('\nDone')
```

Set `status_messages=True` on `stream()` to also receive the orchestrator's
dynamic status lines — short, human-readable progress text distinct from the
fixed-label enrichment phases. (`status_messages` is a streaming-only channel;
`invoke()` does not expose it.)

For product integrations, treat `stream()` as the raw transport layer. If you
are forwarding execution state to a browser or app, normalize the raw events
first:

```python
from maivn.events import normalize_stream

raw_events = agent.stream(
    [HumanMessage(content='Think through this problem')],
    status_messages=True,
)

for event in normalize_stream(raw_events, default_agent_name='assistant'):
    send_to_frontend(event.model_dump())
```

See [Frontend Events](frontend-events.md) for the wire format, ready-made
client examples, and the enrichment phase labels surfaced during a turn.

### ainvoke / astream — the async variants

`ainvoke()` and `astream()` are async wrappers around `invoke()` and `stream()`.
They take the same parameters and dispatch the synchronous work to a worker
thread so you can await them inside an event loop.

```python
# Awaitable single result
response = await agent.ainvoke([HumanMessage(content='Summarize Q1')])

# Async event stream
async for event in agent.astream([HumanMessage(content='Walk me through it')]):
    handle(event)
```

> **Swarm note**
> `Swarm.invoke()` / `Swarm.stream()` and their async variants work the same
> way, but accept either a single message or a sequence, and all tuning
> arguments are keyword-only. See [Multi-Agent](multi-agent.md).

## Reading results

A finished turn returns a `SessionResponse`. The two fields you reach for most
often are `response` and `result`.

| Field         | What it holds                                                        |
| ------------- | -------------------------------------------------------------------- |
| `response`    | The final assistant **text** for the turn                            |
| `result`      | **Structured** output (the final-tool payload), when applicable      |
| `responses`   | All assistant texts emitted during the session                       |
| `metadata`    | Response metadata                                                    |
| `token_usage` | Token usage summary, when available                                  |
| `status` / `error` | Execution status and failure details                            |

Use `.response` when you want the natural-language answer, and `.result` when
the turn produced a typed object you intend to consume programmatically.

```python
# Conversational answer
response = agent.invoke([HumanMessage(content='Summarize this thread.')])
print(response.response)

# Structured answer (see Structured Output guide for the full pattern)
response = agent.invoke(
    [HumanMessage(content='Build the weather report.')],
    force_final_tool=True,
)
print(response.result)  # the validated final-tool object
```

When a turn produces no structured output, `.result` is empty and `.response`
carries the answer. When you force a final tool, `.result` holds the validated
schema. The two are complementary, not exclusive — a turn can populate both.

For per-call token accounting, read `response.token_usage`; see
[Billing and Usage](billing-and-usage.md).

## Tuning a run without internals

Two knobs let you shape the trade-off between speed and depth on a per-call
basis. Most calls should use broad intent hints and let the runtime handle the
details. When you need stricter control, `ModelConfig` lets you scope tier or
exact-model choices to the public configurable framework parts.

### model - tier or scoped model config

```python
response = agent.invoke(
    [HumanMessage(content='Draft a launch plan.')],
    model='balanced',
)
```

| Value        | Intent                                                          |
| ------------ | --------------------------------------------------------------- |
| `'auto'`     | Let the runtime pick an appropriate tier for the request (default) |
| `'fast'`     | Favor speed and lower latency                                   |
| `'balanced'` | A middle ground between speed and capability                    |
| `'max'`      | Favor maximum capability for hard requests                      |
| `'ultra'`    | Favor the highest available capability for the hardest requests |

Leave `model` unset (or `'auto'`) and the runtime chooses for you. The selection
logic is intentionally internal for the broad tier path.

For scoped control, pass a `ModelConfig`:

```python
from maivn import ModelChoice, ModelConfig

response = agent.invoke(
    [HumanMessage(content='Review this incident report and draft the response.')],
    model=ModelConfig(
        response=ModelChoice(model_id='claude-haiku-4-5-20251001'),
        thinking=ModelChoice(tier='max'),
        compose_artifact=ModelChoice(tier='balanced'),
        repl=ModelChoice(tier='fast'),
    ),
)
```

`ModelConfig` supports these parts:

| Part | What it controls |
| ---- | ---------------- |
| `response` | Final/direct assistant response generation |
| `thinking` | The think system tool |
| `compose_artifact` | The compose_artifact system tool |
| `repl` | REPL code generation and repair |

Each part can use `ModelChoice(tier='fast')`, `ModelChoice(tier='balanced')`,
`ModelChoice(tier='max')`, `ModelChoice(tier='ultra')`, `ModelChoice(tier='auto')`, or
`ModelChoice(model_id='...')`. Unspecified parts keep the runtime default.
Internal nodes such as assignment planning and generated-action planning are
not configurable through `ModelConfig`.

To apply one exact model id to every configurable part, use:

```python
response = agent.invoke(
    [HumanMessage(content='Summarize the account.')],
    model=ModelConfig.for_all(model_id='claude-haiku-4-5-20251001'),
)
```

The older `force_model='...'` argument is deprecated. It emits a
`DeprecationWarning` and cannot be combined with `ModelConfig`.

### reasoning — how much deliberation to allow

```python
response = agent.invoke(
    [HumanMessage(content='Reconcile these conflicting reports.')],
    reasoning='high',
)
```

`reasoning` ranges from `'minimal'` through `'low'`, `'medium'`, to `'high'`.
Lower settings return faster; higher settings allow more deliberation for
complex, multi-step reasoning. Like `model`, it is a hint — leave it unset to
use the runtime default.

### metadata — your own labels

`metadata` is a free-form dict for **your** application data: correlation IDs,
request tags, tenant labels, and the like. It is returned on the response and is
useful for tracing and analytics.

```python
response = agent.invoke(
    [HumanMessage(content='Handle ticket 4821.')],
    metadata={'ticket_id': '4821', 'channel': 'email'},
)
```

> **Note**
> `metadata` is for application-owned data only. Reserved runtime-control keys
> are rejected — use the typed config objects (`memory_config`,
> `system_tools_config`, `orchestration_config`) for runtime behavior, not
> `metadata`. See [Session Config](../api/session-config.md).

### verbose — local tracing

`verbose=True` enables legacy terminal tracing for a call. For structured,
filterable event reporting prefer the event builder:

```python
response = agent.events().invoke([HumanMessage(content='Debug this')])
```

See [`events()`](../api/agent.md#events) for the include/exclude categories and
the `on_event` callback.

## targeted_tools and structured_output

Two parameters narrow what a turn does. They cannot both be used at once, and
each has its own interaction with `force_final_tool`.

### targeted_tools — run only these tools

Pass a list of tool names to restrict the turn to those tools (plus their
dependencies):

```python
response = agent.invoke(
    [HumanMessage(content='Run diagnostics.')],
    targeted_tools=['fetch_data', 'analyze_data'],
)
```

### structured_output — guaranteed typed output

Pass a Pydantic type to require the turn to return that schema. The preferred,
public form is the dedicated builder, which routes straight to schema-driven
extraction:

```python
from pydantic import BaseModel, Field

class SentimentAnalysis(BaseModel):
    sentiment: str = Field(..., description='positive, negative, or neutral')
    confidence: float = Field(..., ge=0, le=1)

response = agent.structured_output(SentimentAnalysis).invoke(
    [HumanMessage(content='Analyze: "I love this product!"')]
)
print(response.result)  # a validated SentimentAnalysis instance
```

The `structured_output=` parameter on `invoke()` is the advanced, inline form of
the same idea; for most code prefer `agent.structured_output(Model).invoke(...)`.
See [Structured Output](structured-output.md) for the full comparison with the
`final_tool` pattern.

### Mutual-exclusivity rules

These combinations are validated at call time and raise `ValueError`:

- `force_final_tool` and `targeted_tools` cannot both be set.
- `structured_output` and `targeted_tools` cannot both be set — a fixed output
  schema and a restricted tool set express conflicting intents.
- `force_final_tool=True` requires that a `final_tool` is actually defined.

Pick the single mechanism that matches the turn's goal.

## Running many at once

When you have a list of independent requests, `batch()` runs them concurrently
and returns the responses **in input order**:

```python
batch_inputs = [
    [HumanMessage(content='Summarize ticket A')],
    [HumanMessage(content='Summarize ticket B')],
    [HumanMessage(content='Summarize ticket C')],
]

responses = agent.batch(batch_inputs, max_concurrency=3)
for response in responses:
    print(response.response)
```

- Each input item is passed as the first argument to `invoke()`, so for an
  `Agent` each item is normally a `Sequence[BaseMessage]`.
- `max_concurrency` caps how many run at once (must be `>= 1`). Omit it to use
  the default thread-pool worker count.
- Any keyword `invoke()` accepts can be passed once and is shared by every item
  (for example `model='fast'` or `force_final_tool=True`).
- Order is preserved even when calls finish out of order; an exception in any
  one call is raised to the caller.

`abatch()` is the async variant with the same semantics:

```python
responses = await agent.abatch(batch_inputs, max_concurrency=3)
```

> **Note**
> Batch items are independent runs. Only share a `thread_id` across items when
> you intentionally want concurrent calls writing to the same conversation.

## Scheduling a run later

To run a session on a cadence — cron, a fixed interval, or once at a future
time — use the scheduling builders (`cron`, `every`, `at`) available on every
`Agent` and `Swarm`. The same chain works with `invoke`, `stream`, `batch`, and
the async variants:

```python
job = agent.cron('0 9 * * MON-FRI', tz='America/New_York').invoke(
    [HumanMessage(content='Compose the daily ops briefing.')]
)
```

See the [Scheduled Invocation guide](scheduled-invocation.md) for jitter,
retry, overlap controls, and the lifecycle handle.

## Advanced: introspecting a run

`compile_state()` resolves everything a turn *would* send — the assembled
`SessionRequest` — without executing it. Use it to inspect or debug what an
invocation will do before committing to a run:

```python
request = agent.compile_state([HumanMessage(content='Generate the report.')])
# Inspect the compiled request; nothing has executed yet.
```

This is an introspection aid; it does not start a session.

## Next Steps

- [Structured Output](structured-output.md) — `.structured_output()` and the
  `final_tool` pattern in depth
- [Frontend Events](frontend-events.md) — stream events to any frontend, plus
  the enrichment phase labels
- [Scheduled Invocation](scheduled-invocation.md) — cron, interval, and one-shot
  runs with jitter and retry
- [Memory and Recall](memory-and-recall.md) — how threads and cross-thread
  assets shape what an agent remembers
- [Multi-Agent](multi-agent.md) — running sessions across a `Swarm`
- [Agent API](../api/agent.md) — full `invoke` / `stream` / `batch` reference
- [Swarm API](../api/swarm.md) — the `Swarm` invocation surface
