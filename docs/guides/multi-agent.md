# Multi-Agent Swarms

A swarm is a team of specialist agents that hand work to each other. You describe who does what, who depends on whom, and which member writes the final answer — the runtime handles the coordination.

## What a Swarm Is

Think of a swarm the way you would think of a small project team. One person kicks things off, others contribute their specialty, and one person is responsible for the finished deliverable. A `Swarm` models exactly that:

- Each **member** is an `Agent` with a focused role (research, analysis, writing, review).
- The **first agent** in the swarm is the **entry agent** — the one that receives the incoming request, like a team lead who reads the brief first.
- Members **hand work to each other** through declared dependencies, so an analyst can wait for the researcher's output before starting.
- One member (or one swarm-level tool) is designated to produce the **final output** the caller receives.

Why use a swarm instead of one big agent? Splitting work across specialists keeps each agent's instructions and tools narrow and easy to reason about, lets independent work run in parallel, and makes multi-step workflows explicit instead of buried in one long prompt.

> **A swarm is an `Agent` sibling.** Both `Agent` and `Swarm` are scopes — they share tool registration, structured output, events, batching, and scheduling. If you already know how to drive an `Agent`, a `Swarm` will feel familiar.

## Building a Swarm

Create the member agents first, then group them. The first agent in the `agents` list is the entry agent.

```python
from maivn import Agent, Swarm
from maivn.messages import HumanMessage

researcher = Agent(
    name='researcher',
    description='Finds and gathers information on a topic',
    system_prompt='You research topics thoroughly and cite what you find.',
    api_key='your-api-key',
)

analyst = Agent(
    name='analyst',
    description='Turns research into insights',
    system_prompt='You analyze findings and extract the key takeaways.',
    api_key='your-api-key',
)

writer = Agent(
    name='writer',
    description='Writes the final article',
    system_prompt='You write clear, well-structured content.',
    api_key='your-api-key',
    use_as_final_output=True,  # this member owns the final answer
)

swarm = Swarm(
    name='content_team',
    description='Researches a topic and writes an article about it',
    agents=[researcher, analyst, writer],  # researcher is the entry agent
)
```

Every member needs credentials the same way a standalone `Agent` does — pass an `api_key` or a shared `client` to each agent.

### Managing members after construction

You can also build a swarm incrementally:

```python
swarm = Swarm(name='content_team', agents=[])

swarm.add_agent(researcher)   # the first agent added becomes the entry agent
swarm.add_agent(analyst)
swarm.add_agent(writer)
```

Inspect or look up members at any time:

```python
for agent in swarm.list_agents():
    print(f'{agent.name}: {agent.description}')

# Look up a member by its deterministic id
member = swarm.get_agent(writer.agent_id)
```

`get_agent` takes an `agent_id` (a stable identifier derived from the agent's class name and `name`) and returns the matching member, or `None` if it is not in the swarm. From inside a member's own code you can reach the parent swarm with `agent.get_swarm()`, which returns the swarm it belongs to or `None` if the agent is running standalone.

## Member Registration with Dependencies

Adding an agent to the list registers it as a member. To declare *how* members depend on each other, use the `swarm.member` decorator builder. It registers the agent and attaches dependency metadata to the generated agent-invocation tool — the same vocabulary you already use for tools, applied at the member level.

```python
from maivn import Agent, Swarm, depends_on_tool

swarm = Swarm(name='content_team')

# A swarm-level tool every member can use
@swarm.toolify(description='Load editorial context')
def load_context() -> dict:
    return {'audience': 'technical leaders'}

# Register a member whose invocation depends on a swarm tool's output
@swarm.member
@depends_on_tool(load_context, arg_name='context')
def researcher() -> Agent:
    return Agent(name='researcher', api_key='your-api-key')

# Register a member that depends on another member's output
writer = swarm.member.depends_on_agent(
    researcher,
    arg_name='research_notes',
)(Agent(name='writer', api_key='your-api-key', use_as_final_output=True))
```

The decorated factory returns the registered `Agent`, so the value you get back (`researcher` above) can be referenced by later members' dependencies.

The member builder is chainable and supports the full dependency vocabulary:

| Builder method                     | What it does                                                              |
| ---------------------------------- | ------------------------------------------------------------------------- |
| `swarm.member.depends_on_agent`    | Wait for another member and inject its output                             |
| `swarm.member.depends_on_tool`     | Wait for a tool and inject its result before the member runs              |
| `swarm.member.depends_on_await_for`| Enforce ordering without injecting any data                               |
| `swarm.member.depends_on_reevaluate`| Insert a planning boundary before this member runs                       |
| `swarm.member.depends_on_interrupt`| Collect user input before the member is invoked                           |

```python
from maivn import Agent, Swarm

reviewer = swarm.member.depends_on_agent(
    writer, arg_name='draft',
).depends_on_await_for(
    researcher, timing='after',
)(Agent(name='reviewer', api_key='your-api-key'))
```

> **Private data on members.** Private-data dependencies are not attached directly to member agents. Put the secret access behind a swarm-level tool and have the member depend on that tool. See the cross-links below for the shared dependency vocabulary — it is intentionally not re-explained here.

## Designated Final Output

A swarm produces one final answer. You decide which member owns it.

### One member owns the answer

Set `use_as_final_output=True` on exactly one member. That agent's output becomes the user-facing response.

```python
writer = Agent(
    name='writer',
    api_key='your-api-key',
    use_as_final_output=True,
)
```

At most one member across the swarm may carry this flag. If two do, validation fails when you invoke the swarm.

### A swarm-scope final tool

Instead of designating an agent, you can define a single typed report at the swarm level with `final_tool=True`. The swarm coordinates its members and emits this structured object as the result:

```python
from pydantic import BaseModel

@swarm.toolify(final_tool=True)
class TeamReport(BaseModel):
    """Combined output from the whole team."""
    research_summary: str
    analysis_insights: list[str]
    final_article: str
```

### When you need both

If more than one scope declares a `final_tool` (for example, several members each define one, or a member plus a swarm-scope tool), you must mark exactly one agent `use_as_final_output=True` so the swarm knows whose output is the final response. If only a single scope declares a `final_tool`, that scope automatically owns the answer and no flag is required.

Validation runs when the swarm is invoked. Common errors:

- Two agents both marked `use_as_final_output=True`.
- Multiple scopes declare a `final_tool` but none is designated as the final-output agent.
- The swarm has no agents at all.

### Forcing structured output per member

A member can be required to return a typed report every time it is deployed. Set `force_final_tool=True` on that `Agent` and register its report model with `final_tool=True`. This per-member flag is honored when the member runs inside a swarm.

```python
report_agent = Agent(
    name='report_agent',
    api_key='your-api-key',
    force_final_tool=True,
)

@report_agent.toolify(final_tool=True)
class MemberReport(BaseModel):
    headline: str
    bullets: list[str]
```

## Running a Swarm

A swarm is invoked the same four ways an agent is: `invoke`, `stream`, `ainvoke`, and `astream`. They accept a single message or a sequence of messages.

```python
# Synchronous, returns a SessionResponse
response = swarm.invoke(HumanMessage(content='Write about AI trends in 2026'))

print(response.response)   # final assistant text
print(response.result)     # structured / final-tool output, when present
```

To request a structured final result, pass `force_final_tool=True`. The swarm resolves it against the `use_as_final_output` member or the swarm-scope final tool:

```python
response = swarm.invoke(
    HumanMessage(content='Write about AI trends'),
    force_final_tool=True,
)
report = response.result
```

Continue a conversation across calls with a `thread_id`:

```python
response = swarm.invoke(
    HumanMessage(content='Now make it shorter'),
    thread_id='article-session-123',
)
```

### Keyword-only config

> **Swarm config kwargs are keyword-only.** Unlike `Agent.invoke()`, every option after `messages` on `Swarm.invoke()` / `stream()` / `ainvoke()` / `astream()` must be passed by keyword. Call them as `swarm.invoke(messages, force_final_tool=True)`, never positionally.

The keyword options include `model`, `reasoning`, `force_final_tool`, `stream_response`, `thread_id`, `verbose`, `metadata`, `memory_config`, `system_tools_config`, and `orchestration_config`. Scope-level defaults set on the `Swarm(...)` constructor are merged with any per-call overrides you pass to an invocation.

### Streaming and async

`stream()` yields raw server-sent events as the swarm executes. Pass `status_messages=True` to also receive normalized status-message events for a progress display.

```python
for event in swarm.stream(
    HumanMessage(content='Run the multi-agent workflow'),
    status_messages=True,
):
    handle(event)
```

The async variants mirror the synchronous ones:

```python
response = await swarm.ainvoke(HumanMessage(content='Plan the launch'))

async for event in swarm.astream(HumanMessage(content='Run the workflow')):
    handle(event)
```

For filtered, higher-level event reporting, wrap any invocation with `events()` (inherited from the shared scope):

```python
response = swarm.events(
    include=['enrichment', 'agent', 'model'],
    on_event=lambda payload: post_to_ui(payload),
).invoke(HumanMessage(content='Run the multi-agent workflow'))
```

### How coordination works

Conceptually, the runtime reads each member's declared dependencies and runs the team accordingly: members with no dependencies start first (and run in parallel when they are independent), and a member that depends on another waits for that member's output before it begins. The designated final-output member runs with the upstream results available and produces the answer the caller receives. You declare the data flow; the runtime sequences the work.

```python
from maivn import Agent, Swarm, depends_on_agent

# data_collector and trend_analyzer are independent -> they can run in parallel
# synthesizer depends on both -> it waits for them, then produces the final output
data_collector = Agent(name='data_collector', api_key='your-api-key')
trend_analyzer = Agent(name='trend_analyzer', api_key='your-api-key')
synthesizer = Agent(name='synthesizer', api_key='your-api-key', use_as_final_output=True)

@synthesizer.toolify()
@depends_on_agent(data_collector, arg_name='data')
@depends_on_agent(trend_analyzer, arg_name='trends')
def synthesize(data: dict, trends: dict) -> dict:
    return {'synthesis': 'Combined insights'}

swarm = Swarm(
    name='analysis_team',
    agents=[data_collector, trend_analyzer, synthesizer],
)
```

## Shared Tools and Config Across Members

Tools registered on the swarm itself are available to every member. Use this for utilities the whole team needs, or to wrap private-data access behind a tool that members can depend on.

```python
swarm = Swarm(name='team', agents=[agent1, agent2])

@swarm.toolify(description='Shared utility used by every member')
def shared_utility(data: dict) -> dict:
    return {'processed': True}
```

A `Swarm` also accepts the same typed configuration objects an `Agent` does, applied as defaults for every run and overridable per invocation:

- `memory_config` — default memory behavior for the swarm.
- `system_tools_config` — allowlists and approval controls for system tools.
- `orchestration_config` — orchestration-loop controls.

```python
from maivn import SessionOrchestrationConfig

repair_swarm = Swarm(
    name='repair_team',
    agents=[verifier, editor, director],
    orchestration_config=SessionOrchestrationConfig(
        mode='supervisor_loop',
        final_output_mode='supervised',
        allow_followup_actions=True,
        stop_strategy='objective_satisfied',
        max_cycles=5,
    ),
)
```

In supervised mode, `use_as_final_output=True` means "this member *can* produce the user-facing answer" — not necessarily "stop immediately." The orchestrator can inspect that output and schedule more work until the objective is satisfied or the cycle limit is reached. A supervised swarm may deploy the same member more than once; treat each deployment as a distinct invocation keyed by its assignment id, not by the agent name. Use `final_output_mode='terminal'` for reporting-only swarms whose final-output member should end the run immediately.

## Complete Example

```python
from pydantic import BaseModel
from maivn import Agent, Swarm, depends_on_agent
from maivn.messages import HumanMessage

researcher = Agent(
    name='researcher',
    description='Researches topics and finds relevant information',
    system_prompt='You are a research specialist. Gather information and cite sources.',
    api_key='your-api-key',
)

@researcher.toolify(description='Research a topic thoroughly')
def research_topic(topic: str) -> dict:
    return {
        'topic': topic,
        'findings': [f'Finding about {topic}'],
        'sources': ['source1.com', 'source2.com'],
    }

writer = Agent(
    name='writer',
    description='Writes articles based on research',
    system_prompt='You are a content writer. Turn research into an article.',
    api_key='your-api-key',
    use_as_final_output=True,
)

@writer.toolify(description='Write an article from research')
@depends_on_agent(researcher, arg_name='research')
def write_article(research: dict, style: str = 'professional') -> dict:
    return {
        'title': f"Article about {research['topic']}",
        'body': f"Based on research: {research['findings']}",
        'style': style,
    }

content_team = Swarm(
    name='content_team',
    description='A team that researches and writes articles',
    agents=[researcher, writer],
)

response = content_team.events().invoke(
    HumanMessage(content='Write an article about quantum computing'),
    force_final_tool=True,
)

print(response.result)
```

## Best Practices

- **Give each member one clear job.** Overlapping responsibilities make coordination ambiguous; narrow roles keep prompts and tools focused.
- **Keep dependency chains short.** Prefer a shallow graph (`A -> B -> C`) over a long pipeline; independent members run in parallel and finish sooner.
- **Designate the final output explicitly** whenever more than one scope could produce it.
- **Use event tracing while developing.** `swarm.events().invoke(...)` surfaces what each member is doing and makes coordination easy to follow.

## See Also

- [Tools](tools.md) — how to define and register the tools your members use.
- [Dependencies](dependencies.md) — the shared dependency vocabulary (`depends_on_agent`, `depends_on_tool`, `depends_on_await_for`, `depends_on_reevaluate`, `depends_on_interrupt`) used by both tools and `swarm.member`.
- [Structured Output](structured-output.md) — typed results via `final_tool` + `force_final_tool` and the `structured_output()` fast path.

## Next steps

- [Swarm API](../api/swarm.md) — the full `Swarm` class reference.
- [Agent API](../api/agent.md) — the `Agent` class your members are built from.
