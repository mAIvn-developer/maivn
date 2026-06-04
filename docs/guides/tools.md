# Tools and Dependencies

Tools are how an agent acts in the world, and dependencies are how those actions
compose into a workflow. This guide walks from the simplest idea — "a tool is just
your function the agent can call" — up to declaring that some tools need the output
of others, and letting the system work out the order and what can run at the same time.

If you are new to the SDK, read this top to bottom. If you already know the basics,
jump to [Declaring Dependencies](#declaring-dependencies) or
[DAG Execution at a Glance](#dag-execution-at-a-glance).

## What a Tool Is

A tool is one of your Python functions (or a Pydantic model) that the agent is
allowed to call. You write the function; the agent decides _when_ to call it and
with _what_ arguments, based on the task it was given.

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='helper', api_key='...')

@agent.toolify(description='Get current weather for a city')
def get_weather(city: str) -> dict:
    return {'city': city, 'temp': 72, 'condition': 'sunny'}

response = agent.invoke([HumanMessage(content='What is the weather in Austin?')])
print(response.response)
```

The agent never sees the body of `get_weather`. It only sees a description of the
tool — its name, what it does, and what arguments it takes (derived from your type
hints and docstring). When the agent chooses to call it, **your code runs locally in
your own environment** and only the result is returned. Your business logic and any
data it touches stay on your machine.

> **Two kinds of tools.** A tool is either a **function** that does something and
> returns a value, or a **Pydantic model** that defines a structured shape the agent
> should fill in. Both are registered the same way; the difference is whether the tool
> _executes logic_ or _describes output_.

## The Three Registration Styles

There are three ways to register a tool. All three use the same tool registry and
support the same dependency decorators, so pick whichever reads best for your code.

### 1. Decorator — `@agent.toolify(...)`

Best when you define the tool right next to the agent.

```python
agent = Agent(name='helper', api_key='...')

@agent.toolify(description='Add two numbers together')
def add_numbers(a: int, b: int) -> dict:
    return {'sum': a + b}
```

### 2. Constructor — `Agent(..., tools=[...])`

Best when functions or models already exist and you want the agent's configuration
to show the full tool surface in one place.

```python
def add_numbers(a: int, b: int) -> dict:
    """Add two numbers together."""
    return {'sum': a + b}

agent = Agent(name='helper', api_key='...', tools=[add_numbers])
```

### 3. Imperative — `agent.add_tool(...)`

Best when tools are imported, assembled conditionally, or need extra options.

```python
agent = Agent(name='helper', api_key='...')
agent.add_tool(
    add_numbers,
    name='add_numbers',
    description='Add two numbers together',
    tags=['math'],
)
```

`add_tool` also accepts `always_execute=True` (pin the tool so it runs at least once),
`final_tool=True` (mark the structured-output tool — see
[Structured Output](structured-output.md)), and an `override=ToolOverride(...)` to
reframe a generic tool for one app without editing the provider code.

```python
from maivn import ToolOverride

agent.add_tool(
    search,
    override=ToolOverride(
        name='inbox_search',
        description='Search recent inbox messages for triage.',
        tags=['email', 'read'],
        default_args={'max_results': 10},
    ),
)
```

`ToolOverride` fields are optional. Scalars (`name`, `description`, `always_execute`,
`final_tool`) replace the registered value; `tags` and `dependencies` append;
`metadata` and `default_args` merge. Model-supplied arguments always win over defaults
at execution time.

## Tool Types

### Function Tools

A function tool runs logic and returns a value. The SDK serializes the return value
(dict, list, primitive, Pydantic model, dataclass, set, or tuple) before handing it
back to the agent.

```python
@agent.toolify(description='Fetch current weather data for any city worldwide')
def get_weather(city: str) -> dict:
    return {'city': city, 'temp': 72}
```

Two things make a function a good tool:

- **Type hints.** The JSON schema the agent sees is built from your signature. Use
  `Annotated[...]` with a short description or a Pydantic `Field(...)` to add
  constraints the agent can read and that Pydantic enforces at call time.
- **A clear description.** Pass `description=` or rely on the docstring. Say _when_ to
  use the tool, what the arguments mean, and what shape it returns.

```python
from typing import Annotated
from pydantic import Field

@agent.toolify()
def dispatch_vehicle(
    vehicle_id: Annotated[
        str,
        Field(description="A single vehicle ID like 'VAN-103'.", pattern=r'^VAN-\d+$'),
    ],
    estimated_km: Annotated[int, Field(description='Trip distance in km.', gt=0)],
) -> dict:
    ...
```

Async functions work the same way — just declare `async def`.

See the [deep reference below](#deep-references) for return-value conventions, error
handling, and parameter-documentation guidance.

### Pydantic-Model Tools

A model tool describes a structured shape for the agent to produce. Register the model
the same way you register a function; the class docstring becomes the description and
each `Field(description=...)` guides the agent's output.

```python
from pydantic import BaseModel, Field

@agent.toolify(description='Generate a weather report')
class WeatherReport(BaseModel):
    """Structured weather report."""
    city: str = Field(..., description='City name')
    temperature: int = Field(..., description='Temperature in Fahrenheit')
    conditions: str = Field(..., description='Weather conditions')
```

Model tools support nested models to any depth, lists of models, and optional fields.
Marking one with `final_tool=True` makes it the agent's guaranteed typed answer — see
[Structured Output](structured-output.md).

### Prebuilt `BaseTool`

If you already hold a prebuilt tool object (a `BaseTool` instance — for example one
produced by a connected MCP server), pass it to any of the three registration styles
just like a function or model. The registry treats all three uniformly.

## Toolsets: Register Many Methods at Once

When several related tools share configuration — a database handle, an HTTP client, an
OAuth token — group them on one class instead of scattering free functions. Decorate
the class with `@toolset(prefix=...)`, mark each tool method with `@toolify(...)`, then
register the instance with `agent.add_toolset(instance)`.

```python
from maivn import Agent, PermissionFlag, PermissionSet, toolify, toolset


@toolset(prefix='reports')
class WeeklyReportsToolSet:
    """Pull weekly KPI reports from an internal warehouse."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    @toolify(permissions=PermissionSet(PermissionFlag.READ))
    def weekly_signups(self, week_of: str) -> list[dict]:
        """Signup counts by source for the week starting ``week_of`` (YYYY-MM-DD)."""
        ...

    @toolify(permissions=PermissionSet(PermissionFlag.READ))
    def revenue_per_signup(self, week_of: str) -> float:
        """Compute revenue per signup for ``week_of``."""
        ...


agent = Agent(name='analyst', api_key='...')
agent.add_toolset(WeeklyReportsToolSet('/srv/warehouse.db'))
```

After `add_toolset` returns, the agent has two new tools: `REPORTS_weekly_signups` and
`REPORTS_revenue_per_signup`.

### Name Prefixing

The `prefix` is uppercased and joined to the method name with an underscore. The
uppercase form keeps generated names within the provider tool-name regex
(`^[a-zA-Z0-9_-]{1,64}$`) and makes the namespace boundary readable at a glance in
logs and traces.

### Filtering with include / exclude and tags

`add_toolset` accepts four optional filters so one class can be narrowed at
registration time. A method must pass _every_ active filter.

```python
agent.add_toolset(instance, include_tags=['read'])        # only read-tagged methods
agent.add_toolset(instance, exclude_tags=['destructive']) # skip destructive methods
agent.add_toolset(instance, include=['weekly_signups'])   # whitelist method names
agent.add_toolset(instance, exclude=['revenue_per_signup'])  # skip method names
```

Tags are auto-derived from each method's `permissions=` and `destructive=` markers
(`PermissionSet(PermissionFlag.READ)` adds `"read"`; `PermissionFlag.DELETE` adds both
`"delete"` and `"destructive"`), and any `tags=` you declare on `@toolset` / `@toolify`
are merged on top. `include` / `exclude` match the **unprefixed** method name (or a
`name=` alias from `@toolify`).

### Overrides

Toolsets accept `overrides={...}` with the same `ToolOverride` shape used by direct
tools and MCP tools. Keep provider classes generic, then retarget names, descriptions,
defaults, dependencies, and tags for the app using them.

```python
from maivn import ToolOverride

agent.add_toolset(
    WeeklyReportsToolSet('/srv/warehouse.db'),
    overrides={
        'weekly_signups': ToolOverride(
            description='Fetch weekly signup counts for the KPI dashboard.',
            default_args={'week_of': '2026-05-11'},
            tags=['dashboard'],
        ),
    },
)
```

Override keys are the unprefixed Python method name or the `@toolify(name=...)` alias.
This is the same pattern used by the `maivn-tools` package — see
[its toolsets guide](https://github.com/mAIvn-developer/maivn-tools/blob/main/docs/toolsets.md)
for the full filter API and decorator reference.

## Tool Execution Hooks

Every tool can run a callback _before_ and _after_ it executes — useful for logging,
auditing, or timing.

```python
def log_start(ctx):
    print(f"Starting: {ctx['tool_id']}")

def log_end(ctx):
    print(f"Finished: {ctx['tool_id']}, result: {ctx['result']}")

@agent.toolify(before_execute=log_start, after_execute=log_end)
def my_tool() -> dict:
    return {'done': True}
```

Hooks never abort execution: if a hook raises, the failure is reported but the tool
still runs. Each firing emits a normalized `hook_fired` event (carrying the hook name,
the `before`/`after` stage, a `completed`/`failed` status, and elapsed time) that
custom frontends can subscribe to via the events API.

### Per-tool vs per-scope: `hook_execution_mode`

By default, hooks fire once per tool call. Set `hook_execution_mode` on the `Agent`
(or `Swarm`) to change that:

- `'tool'` (default) — fire for each tool execution.
- `'scope'` — fire once per `invoke()` / `stream()` call.
- `'agent'` — alias for `'scope'`.

```python
agent = Agent(
    name='auditor',
    api_key='...',
    before_execute=audit_log,     # fires per-tool by default
    hook_execution_mode='scope',  # now fires once per invoke()/stream()
)
```

## Declaring Dependencies

So far each tool stands alone. The next step is composition: **some tools need the
output of other tools.** A report tool needs the data a fetch tool produced; a write
tool needs to run after a validation tool.

You declare these relationships with decorators. You never write the scheduling code
yourself — **you describe what each tool needs, and the system derives the execution
order and what can run concurrently** from those declarations.

```python
from maivn import Agent, depends_on_tool

agent = Agent(name='data_agent', api_key='...')

@agent.toolify(description='Fetch raw data from source')
def fetch_data(source: str) -> dict:
    return {'source': source, 'records': [1, 2, 3]}

@agent.toolify(description='Process fetched data')
@depends_on_tool(fetch_data, arg_name='raw_data')
def process_data(raw_data: dict) -> dict:
    return {'processed': len(raw_data['records'])}
```

When `process_data` is needed, `fetch_data` runs first and its output is passed into
the `raw_data` argument. You did not order them — the dependency declaration did.

> **Dependencies are independent of registration style.** The same graph works whether
> you register with `@agent.toolify`, `Agent(..., tools=[...])`, or `agent.add_tool`.

### `@depends_on_tool` — output from another tool

Inject one tool's result into another tool's argument. Stack the decorator to depend on
several tools at once; independent ones run in parallel.

```python
@agent.toolify()
def fetch_users() -> dict:
    return {'users': [...]}

@agent.toolify()
def fetch_orders() -> dict:
    return {'orders': [...]}

@agent.toolify()
@depends_on_tool(fetch_users, arg_name='users')
@depends_on_tool(fetch_orders, arg_name='orders')
def generate_report(users: dict, orders: dict) -> dict:
    return {'report': 'Combined users and orders'}
```

`fetch_users` and `fetch_orders` have no dependency on each other, so they run at the
same time; `generate_report` waits for both.

### `@depends_on_agent` — output from another agent

In a multi-agent system, a tool can depend on the full output of another agent. The
dependency agent runs first, and its result is passed into the dependent tool.

```python
from maivn import Agent, Swarm, depends_on_agent

researcher = Agent(name='researcher', api_key='...')
writer = Agent(name='writer', api_key='...')

@researcher.toolify(description='Research a topic')
def research(topic: str) -> dict:
    return {'findings': f'Research on {topic}'}

@writer.toolify(description='Write based on research')
@depends_on_agent(researcher, arg_name='research_result')
def write_article(research_result: dict) -> dict:
    return {'article': f'Based on: {research_result}'}

swarm = Swarm(name='team', agents=[researcher, writer])
```

See the [Multi-Agent Guide](multi-agent.md) for swarm-member dependencies and
agent-to-agent handoffs.

> **Injecting secrets.** A related decorator, `@depends_on_private_data`, injects
> private values into a tool argument _at execution time_ — the value is never
> shown to the model and is redacted from results. It is covered in the
> [Private Data Guide](private-data.md).

## Gating and Replanning

Two more controls shape _ordering_ and _planning_ without injecting any data. They are
metadata-only: they affect when a tool runs, not what arguments it receives.

### `@depends_on_await_for` — order without data flow

Use this when a tool must wait for another even though it does not consume that tool's
return value — ordering, confirmation flows, audit trails, side-effecting tools.

```python
from maivn import depends_on_await_for

@agent.toolify(description='Fetch latest records')
def fetch_records() -> dict:
    return {'records': [...]}

@agent.toolify(description='Write compliance audit entry after fetch')
@depends_on_await_for(fetch_records, timing='after', instance_control='all')
def write_audit_log() -> dict:
    return {'logged': True}
```

`timing` (`'before'` / `'after'`) and `instance_control` (`'each'` / `'all'`) are
keyword-only and let you express "after every matching call" versus "after all of them
complete."

### `@depends_on_reevaluate` — pause, inspect, and replan

Use this when planning should stop at a boundary, look at the results gathered so far,
and then plan the next step using that real output instead of placeholder arguments.

```python
from maivn import depends_on_reevaluate

@agent.toolify(description='Fetch document text')
def fetch_document() -> dict:
    return {'text': '...'}

@agent.toolify(description='Create final summary after reviewing fetched content')
@depends_on_reevaluate(fetch_document, timing='after', instance_control='all')
def summarize_document() -> dict:
    return {'summary': '...'}
```

This is a firm boundary, not a hint: the runtime ensures the replanning step happens at
the declared point so the dependent tool only runs once the earlier result is genuinely
in scope. When a replanning cycle fires, the SDK surfaces a `reevaluate_accrued`
enrichment event you can observe in the stream.

## DAG Execution at a Glance

When you stack these decorators you are, in effect, describing a **directed acyclic
graph** (DAG) — a set of steps where edges point from a tool to the tools that depend
on it. You declare the edges; the system figures out the rest.

```python
# Level 1: no dependencies
@agent.toolify()
def step_a() -> dict:
    return {'step': 'A'}

@agent.toolify()
def step_b() -> dict:
    return {'step': 'B'}

# Level 2: depends on level 1
@agent.toolify()
@depends_on_tool(step_a, 'a_result')
def step_c(a_result: dict) -> dict:
    return {'step': 'C', 'from': a_result}

@agent.toolify()
@depends_on_tool(step_b, 'b_result')
def step_d(b_result: dict) -> dict:
    return {'step': 'D', 'from': b_result}

# Level 3: depends on level 2
@agent.toolify(final_tool=True)
@depends_on_tool(step_c, 'c_result')
@depends_on_tool(step_d, 'd_result')
class FinalReport(BaseModel):
    combined: str
```

From those declarations the system derives the schedule:

1. `step_a` and `step_b` are independent, so they **run concurrently**.
2. `step_c` and `step_d` each **wait** for their single dependency, then run.
3. `FinalReport` runs last, once both branches complete.

The two guarantees worth remembering:

- **Independent branches run at the same time.** If two tools share no dependency,
  there is nothing forcing them to take turns. Three two-second fetches with no edges
  between them finish in about two seconds, not six.
- **Dependent steps wait.** A tool never starts until everything it declared a
  dependency on has produced a result.

You get parallelism for free and correct ordering by construction — without writing any
orchestration code, threads, or `await` chains yourself. **Circular dependencies are
detected and rejected**, and the decorators validate at import time that each
`arg_name` actually exists in the target's signature, so misconfigured graphs fail
loudly instead of misbehaving at runtime.

## Deep References

This page is the conceptual spine. For the full option-by-option detail, see:

- [Dependencies Guide](dependencies.md) — every dependency and control decorator with
  validation rules, combined-dependency examples, and execution-timing walkthroughs.
- [Decorators API](../api/decorators.md) — exact signatures, parameter tables, and the
  full toolset decorator reference (`@toolset`, `@toolify`, permission helpers,
  `ProviderMetadata`, `ToolOverride`).
- [Agent API](../api/agent.md) — `toolify()`, `add_tool(...)`, `add_toolset(...)`,
  `tools=[...]`, and the scope-hook matrix.

## Next Steps

- [Structured Output](structured-output.md) — turn a Pydantic model into a guaranteed
  typed answer with `final_tool` and `structured_output()`.
- [Private Data](private-data.md) — inject secrets into tools without ever exposing
  them to the model.
- [Multi-Agent](multi-agent.md) — compose agents into a Swarm and wire dependencies
  across agent boundaries.
