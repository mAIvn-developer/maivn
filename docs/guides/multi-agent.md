# Multi-Agent Guide

Coordinate multiple specialized agents using Swarms for complex workflows.

## Overview

Multi-agent systems are useful when:
- Tasks require different areas of expertise
- Work can be parallelized across specialized agents
- Complex workflows need coordination

The maivn SDK provides:
- `Swarm` class for agent coordination
- `swarm.member` for declarative swarm member registration
- `@depends_on_agent` for agent-to-agent dependencies
- `use_as_final_output` for designating the final agent

## Creating a Swarm

### Basic Swarm

```python
from maivn import Agent, Swarm
from maivn.messages import HumanMessage

# Create specialized agents
researcher = Agent(
    name='researcher',
    description='Expert at finding information',
    system_prompt='You research topics thoroughly.',
    api_key='...',
)

analyst = Agent(
    name='analyst',
    description='Expert at analyzing data',
    system_prompt='You analyze data and extract insights.',
    api_key='...',
)

writer = Agent(
    name='writer',
    description='Expert at writing content',
    system_prompt='You write clear, engaging content.',
    api_key='...',
    use_as_final_output=True,
    included_nested_synthesis='auto',
)

# Create swarm
swarm = Swarm(
    name='content_team',
    description='Team for creating research-based content',
    agents=[researcher, analyst, writer],
)
```

### Member Registration

Use `swarm.member` when you want the swarm to register agents near their definitions and attach
member-level dependency metadata.

```python
from maivn import Agent, Swarm, depends_on_tool

swarm = Swarm(name='content_team')

@swarm.toolify(description='Load editorial context')
def load_context() -> dict:
    return {'audience': 'technical leaders'}

@swarm.member
@depends_on_tool(load_context, arg_name='context')
def researcher() -> Agent:
    return Agent(name='researcher', api_key='...')

writer = swarm.member.depends_on_agent(
    researcher,
    arg_name='research_notes',
)(Agent(name='writer', api_key='...', use_as_final_output=True))
```

The decorated factory returns the registered `Agent`, so it can be passed to
`swarm.member.depends_on_agent(...)` or normal `@depends_on_agent(...)` dependencies.

## Agent Dependencies

Use `@depends_on_agent` to chain agent outputs:

```python
from maivn import depends_on_agent

# Researcher's tool
@researcher.toolify(description='Research a topic')
def research(topic: str) -> dict:
    return {'findings': f'Research findings about {topic}'}

# Analyst depends on researcher
@analyst.toolify(description='Analyze research findings')
@depends_on_agent(researcher, arg_name='research_data')
def analyze(research_data: dict) -> dict:
    return {'analysis': f'Analysis of {research_data}'}

# Writer depends on analyst
@writer.toolify(description='Write article from analysis')
@depends_on_agent(analyst, arg_name='analysis_data')
def write_article(analysis_data: dict) -> dict:
    return {'article': f'Article based on {analysis_data}'}
```

### Execution Flow

1. `researcher` is invoked first (no dependencies)
2. `analyst` runs after receiving researcher's output
3. `writer` runs last and produces the final output

## Invoking a Swarm

### Basic Invocation

```python
response = swarm.invoke(
    HumanMessage(content='Write about AI trends in 2024'),
)
```

### With Force Final Tool

```python
response = swarm.invoke(
    HumanMessage(content='Write about AI trends'),
    force_final_tool=True,
)
```

### With Thread ID

```python
response = swarm.invoke(
    HumanMessage(content='Continue the article'),
    thread_id='article-session-123',
)
```

## Final Output Patterns

### Pattern 1: Final Output Agent

Designate one agent to produce the final output:

```python
writer = Agent(
    name='writer',
    api_key='...',
    use_as_final_output=True,  # Only ONE agent can have this
)
```

### Pattern 2: Swarm-Level Final Tool

Define a final tool at the swarm level:

```python
from pydantic import BaseModel, Field

@swarm.toolify(final_tool=True)
class TeamReport(BaseModel):
    """Combined output from all agents."""
    research_summary: str
    analysis_insights: list[str]
    final_article: str
```

**Note**: You cannot use both patterns in the same swarm.

## Nested Synthesis Control

For swarm member agents, `included_nested_synthesis` controls whether nested synthesis text is included or only raw tool results are passed:

```python
researcher = Agent(
    name='researcher',
    api_key='...',
    included_nested_synthesis=False,  # Prefer raw tool results
)

analyst = Agent(
    name='analyst',
    api_key='...',
    included_nested_synthesis='auto',  # Let swarm orchestrator/runtime decide
)
```

- `True`: always include synthesized nested response
- `False`: skip nested synthesis
- `'auto'` (default): root swarm orchestration/runtime decides based on context and payload size

## Parallel Agent Execution

When agents don't depend on each other, they run in parallel:

```python
# These agents can run simultaneously
data_collector = Agent(name='data_collector', api_key='...')
trend_analyzer = Agent(name='trend_analyzer', api_key='...')

# This agent depends on both
synthesizer = Agent(
    name='synthesizer',
    api_key='...',
    use_as_final_output=True,
)

@data_collector.toolify()
def collect_data() -> dict:
    return {'data': [...]}

@trend_analyzer.toolify()
def analyze_trends() -> dict:
    return {'trends': [...]}

@synthesizer.toolify()
@depends_on_agent(data_collector, arg_name='data')
@depends_on_agent(trend_analyzer, arg_name='trends')
def synthesize(data: dict, trends: dict) -> dict:
    return {'synthesis': 'Combined insights'}

swarm = Swarm(
    name='analysis_team',
    agents=[data_collector, trend_analyzer, synthesizer],
)

# data_collector and trend_analyzer run in parallel
# synthesizer waits for both to complete
```

## Swarm-Level Tools

Tools registered on the swarm are available to all agents:

```python
swarm = Swarm(name='team', agents=[agent1, agent2])

@swarm.toolify(description='Shared utility function')
def shared_utility(data: dict) -> dict:
    return {'processed': True}

# Both agent1 and agent2 can use shared_utility
```

## Managing Agents

### Adding Agents

```python
swarm = Swarm(name='team', agents=[])

# Add agents after creation
swarm.add_agent(researcher)
swarm.add_agent(analyst)

# Or register agents declaratively
@swarm.member
def writer() -> Agent:
    return Agent(name='writer', api_key='...')
```

### Listing Agents

```python
for agent in swarm.list_agents():
    print(f'{agent.name}: {agent.description}')
```

### Getting an Agent

```python
agent = swarm.get_agent(agent_id)
```

## Validation

### Single Final Output

When more than one agent (or the swarm itself) declares a `final_tool`, exactly one
agent must be marked `use_as_final_output=True` to disambiguate which agent owns the
swarm's final response. The check runs at swarm validation time:

```python
# Error: two agents marked use_as_final_output, validated when swarm.invoke runs
agent1 = Agent(..., use_as_final_output=True)
agent2 = Agent(..., use_as_final_output=True)

swarm = Swarm(agents=[agent1, agent2])
swarm.invoke(...)  # Error: Multiple swarm agents marked use_as_final_output=True
```

If only one agent declares a `final_tool`, no `use_as_final_output` flag is required —
that agent automatically owns the final response.

### Swarm Must Have Agents

```python
swarm = Swarm(name='empty', agents=[])
swarm.invoke(...)  # Error: Swarm.invoke requires at least one Agent in the swarm.
```

## Complete Example

```python
from pydantic import BaseModel, Field
from maivn import Agent, Swarm, depends_on_agent
from maivn.messages import HumanMessage

# Research agent
researcher = Agent(
    name='researcher',
    description='Researches topics and finds relevant information',
    system_prompt='''You are a research specialist.
    Use the research tool to gather information about topics.''',
    api_key='your-api-key',
)

@researcher.toolify(description='Research a topic thoroughly')
def research_topic(topic: str, depth: str = 'comprehensive') -> dict:
    return {
        'topic': topic,
        'depth': depth,
        'findings': [
            f'Finding 1 about {topic}',
            f'Finding 2 about {topic}',
            f'Finding 3 about {topic}',
        ],
        'sources': ['source1.com', 'source2.com'],
    }

# Writer agent
writer = Agent(
    name='writer',
    description='Writes articles based on research',
    system_prompt='''You are a content writer.
    Use the write_article tool to create content from research.''',
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

# Create the swarm
content_team = Swarm(
    name='content_team',
    description='A team that researches and writes articles',
    system_prompt='Coordinate research and writing tasks.',
    agents=[researcher, writer],
)

# Invoke the swarm
response = content_team.events().invoke(
    HumanMessage(content='Write an article about quantum computing'),
    force_final_tool=True,
)

print(response.result)
```

## Best Practices

### 1. Clear Agent Responsibilities

Each agent should have a clear, focused role:

```python
# Good: clear responsibilities
researcher = Agent(name='researcher', description='Finds information')
analyst = Agent(name='analyst', description='Analyzes data')
writer = Agent(name='writer', description='Writes content')

# Avoid: overlapping responsibilities
agent1 = Agent(name='agent1', description='Does research and analysis')
agent2 = Agent(name='agent2', description='Does analysis and writing')
```

### 2. Minimal Dependencies

Keep dependency chains as short as practical:

```python
# Prefer this
A -> B -> C (3 agents)

# Over this
A -> B -> C -> D -> E -> F (6 agents)
```

### 3. Descriptive System Prompts

```python
researcher = Agent(
    name='researcher',
    system_prompt='''You are a research specialist.
    Your job is to find accurate, relevant information.
    Always cite your sources.
    Use the research_topic tool for all research tasks.''',
    api_key='...',
)
```

### 4. Use Event Tracing for Debugging

```python
response = swarm.events().invoke(
    message,
)
```

## See Also

- [Swarm API](../api/swarm.md) - Swarm class reference
- [Agent API](../api/agent.md) - Agent class reference
- [Dependencies Guide](dependencies.md) - `@depends_on_agent` details
