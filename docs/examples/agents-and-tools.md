# Agents & Tools

The three ways to attach tools to an agent, plus the dependency and hook
mechanics that go on top.

## Tool registration: three equivalent forms

### 1. Decorator — `@agent.toolify(...)`

The most common form. Define and register in one step:

```python
agent = Agent(name='Support Planner', system_prompt='...', api_key='...')

@agent.toolify(name='fetch_device_telemetry')
def fetch_device_telemetry(device_id: str) -> dict:
    return {'device_id': device_id, 'temperature_c': 84.2, 'error_rate': 0.17}
```

### 2. Imperative — `agent.add_tool(...)`

When you want to define tools elsewhere and register them on an existing
agent (useful for libraries of reusable tools):

```python
def fetch_device_telemetry(device_id: str) -> dict:
    return {'device_id': device_id, 'temperature_c': 84.2}

def triage_device_issue(telemetry: dict) -> dict:
    return {'priority': 'high' if telemetry['temperature_c'] > 80 else 'normal'}

class ResolutionPlan(BaseModel):
    device_id: str
    priority: str
    recommended_action: str

agent = Agent(name='Support Planner', system_prompt='...', api_key='...')

agent.add_tool(fetch_device_telemetry, tags=['telemetry'])
agent.add_tool(triage_device_issue, name='triage_device_issue')
agent.add_tool(ResolutionPlan, name='write_resolution_plan', final_tool=True)
```

### 3. Constructor — `Agent(..., tools=[...])`

When the tool list is known up front and you'd rather express it in one place:

```python
def load_account_profile(account_id: str) -> dict:
    return {'account_id': account_id, 'tier': 'enterprise', 'renewal_days': 42}

def load_product_usage(account_id: str) -> dict:
    return {'active_users': 118, 'support_tickets': 3}

@depends_on_tool(load_account_profile, arg_name='profile')
@depends_on_tool(load_product_usage, arg_name='usage')
def build_account_brief(profile: dict, usage: dict) -> dict:
    return {
        'account': profile['account_id'],
        'health': 'green' if usage['support_tickets'] <= 3 else 'yellow',
    }

agent = Agent(
    name='Account Success Agent',
    system_prompt='...',
    api_key='...',
    tools=[load_account_profile, load_product_usage, build_account_brief],
)
```

All three forms produce the same registered tools — pick whichever fits
your code organization.

## Listing what's registered

```python
for tool in agent.list_tools():
    print(f'{tool.name}: {tool.description}')
```

Useful inside a CLI or admin route to introspect what an agent can do.

## Cross-agent dependencies

A tool can declare that calling it implicitly invokes another agent and
receives that agent's output. This is `@depends_on_agent`:

```python
from maivn import Agent, depends_on_agent

data_analyzer = Agent(
    name='Data Analyzer',
    system_prompt='You analyze datasets with the analyze_dataset tool.',
    api_key='...',
)

@data_analyzer.toolify(name='analyze_dataset')
def analyze_dataset(dataset_name: str) -> dict:
    return {'dataset': dataset_name, 'mean': 42.5, 'stddev': 12.3, 'sample_size': 1000}

research_coordinator = Agent(
    name='Research Coordinator',
    system_prompt='Coordinate research. Call generate_research_report at the end.',
    api_key='...',
)

@depends_on_agent(data_analyzer, arg_name='analysis_result')
@research_coordinator.toolify(name='generate_research_report')
class ResearchReport(BaseModel):
    title: str
    dataset_info: dict
    analysis_result: dict  # automatically injected from data_analyzer's output
    conclusions: list[str]
```

When `generate_research_report` is constructed, the runtime invokes
`data_analyzer` first and passes its final result in as `analysis_result`.
The coordinator never has to know how the analyzer works — it just sees a
field that gets filled in.

### Required vs. optional agent dependencies

`@depends_on_agent(agent_ref, arg_name='...')` is **required** — the
upstream agent is always invoked. Mark the dependency optional and the LLM
decides whether to trigger it:

```python
@depends_on_agent(data_analyzer, arg_name='analysis_result', required=False)
@research_coordinator.toolify(name='generate_research_report')
class ResearchReport(BaseModel):
    ...
```

## Tool execution hooks

`before_execute` and `after_execute` callbacks fire around every tool
invocation. Useful for audit logging, metrics, retries, or correlation IDs.

### Per-tool hooks

Pass them through `toolify`:

```python
def log_before(payload: dict) -> None:
    print(f'[BEFORE] tool={payload["tool"].name} args={payload.get("args")}')

def log_after(payload: dict) -> None:
    if payload.get('error'):
        print(f'[AFTER] tool={payload["tool"].name} FAILED: {payload["error"]}')
    else:
        print(f'[AFTER] tool={payload["tool"].name} ok in {payload.get("elapsed_ms")}ms')

@agent.toolify(
    name='extract_ticket',
    before_execute=log_before,
    after_execute=log_after,
)
def extract_ticket(ticket: str) -> dict:
    if 'ERROR' in ticket.upper():
        raise RuntimeError('ticket contains ERROR')
    return {'customer': 'Acme Co', 'priority': 'P2'}
```

### Scope-level hooks (every tool the agent or swarm runs)

Set `before_execute` / `after_execute` directly on the `Agent` or `Swarm` to
hook every tool that runs in that scope:

```python
agent.before_execute = log_before
agent.after_execute = log_after

# Or on a swarm — fires for every tool every member agent runs.
swarm.before_execute = log_before
swarm.after_execute = log_after
```

### Tuning when scope-level hooks fire

`hook_execution_mode` controls how often the scope-level hooks run:

- `"tool"` (default) — once per tool execution.
- `"scope"` — once per invocation of this scope.
- `"agent"` (swarm only) — once per agent execution inside the swarm.

```python
swarm.hook_execution_mode = 'agent'   # one fire per agent run, not per tool
```

The hook payload includes:

| Key | Meaning |
| --- | --- |
| `stage` | `"before"` or `"after"` |
| `tool` | The tool descriptor (`.name`, `.description`, …) |
| `tool_id` | A stable id for this execution |
| `args` | Resolved arguments about to be passed in |
| `result` | The tool's return value (after only) |
| `error` | The exception, if the tool raised (after only) |
| `elapsed_ms` | Wall time the tool took (after only) |

Hooks that fire are surfaced inline in mAIvn Studio on the owning card
(tool / agent / swarm) so you can verify behavior without tailing logs.

## Datetime awareness

By default, the LLM has no idea what time or timezone it is. Configure the
`Client` once and every invocation receives a timestamp + zone:

```python
from maivn import Agent, Client

client = Client(api_key='...', client_timezone='America/New_York')

agent = Agent(name='Scheduler Assistant', system_prompt='...', client=client)
agent.invoke([HumanMessage(content='What does my afternoon look like?')])
```

The agent sees a system-injected timestamp in RFC 3339 format with the zone
name, so prompts like "next Tuesday" or "this afternoon" resolve correctly.

## What's next

- **[Swarms](./swarms.md)** — multiple agents working together.
- **[Tools guide](../guides/tools.md)** — full decorator reference.
- **[Private Data](./private-data.md)** — dependency injection for sensitive
  values.
