# Memory

Agents can remember prior turns, extract reusable skills and insights, and
retrieve attached resources at execution time. Memory is opt-in and
configurable per agent.

## Enabling memory

The simplest configuration: turn it on, pick a level, let defaults handle
the rest.

```python
from maivn import Agent, MemoryConfig

agent = Agent(
    name='Memory Demo Agent',
    system_prompt='You are an analyst. Use prior context when available.',
    api_key='...',
    memory_config=MemoryConfig(enabled=True, level='clarity'),
)
```

Memory `level` controls what gets persisted:

| Level | Persists |
| --- | --- |
| `'thread'` | Just the thread's own conversation. |
| `'clarity'` | Thread context + extracted skills and insights. |
| `'graph'` | Adds graph relationships for cross-thread retrieval. |

For day-to-day work, `clarity` is the sweet spot.

## A seed-and-recall pattern

Drop fact-dense context into the first turn; ask for recall in a second
turn on the same thread:

```python
from maivn import Agent, MemoryConfig, MemoryRetrievalConfig
from maivn.messages import HumanMessage

agent = Agent(
    name='Memory Demo Agent',
    system_prompt='You are an operations analyst. Use prior context when available.',
    api_key='...',
    memory_config=MemoryConfig(
        enabled=True,
        level='clarity',
        persistence_mode='vector_plus_graph',
        retrieval=MemoryRetrievalConfig(top_k=2, candidate_limit=2),
    ),
)

thread_id = 'project-coastal-shield'

# Turn 1: seed
agent.invoke(
    [HumanMessage(content=(
        'Project Coastal Shield priorities:\n'
        '1) Salt marsh restoration — owner: Lena Brooks, deadline 2027-03-15\n'
        '2) Stormwater pump upgrade — owner: Marcus Lee, deadline 2028-08-30\n'
        '3) Community cooling centers — owner: Priya Raman, deadline 2029-05-01\n'
        'Budget cap: $185M. Escalate if sea-level trend exceeds 8mm/year.'
    ))],
    thread_id=thread_id,
)

# Turn 2: recall on the same thread
response = agent.invoke(
    [HumanMessage(content='List the priorities, owners, deadlines, and escalation trigger.')],
    thread_id=thread_id,
)
```

The same `thread_id` puts both invocations on the same conversation; memory
retrieval surfaces the seed turn's facts when the recall turn asks for them.

## Full retrieval configuration

For finer control over how much context is pulled in:

```python
from maivn import (
    MemoryConfig,
    MemoryRetrievalConfig,
    MemorySkillExtractionConfig,
    MemoryInsightExtractionConfig,
)

memory_config = MemoryConfig(
    enabled=True,
    level='clarity',
    summarization_enabled=True,
    persistence_mode='vector_plus_graph',
    retrieval=MemoryRetrievalConfig(
        skills_enabled=True,
        insights_enabled=True,
        resources_enabled=True,
        skill_injection_max_count=5,
        insight_injection_max_count=5,
        resource_injection_max_count=5,
        top_k=6,
        candidate_limit=12,
        insight_relevance_floor=0.0,
    ),
    skill_extraction=MemorySkillExtractionConfig(
        enabled=True,
        sharing_scope='project',
    ),
    insight_extraction=MemoryInsightExtractionConfig(
        enabled=True,
        sharing_scope='agent',
    ),
)
```

- **Skills** — reusable procedural patterns the agent learns over time.
  `sharing_scope='project'` means every agent in the project can use them.
- **Insights** — declarative facts ("this customer prefers async over
  email"). `sharing_scope='agent'` keeps them private to one agent.

## Bound skills

Pre-supply skills the agent should always know about, instead of waiting
for them to be extracted from conversation:

```python
BOUND_SKILLS = [
    {
        'skill_id': 'general_research_workflow_v1',
        'name': 'general_research_workflow',
        'description': (
            'For any user question, gather context, synthesize findings, then '
            'answer with concise bullets plus optional next steps.'
        ),
        'steps': [
            {'index': 1, 'action': 'identify core question and constraints'},
            {'index': 2, 'action': 'extract relevant evidence from memory'},
            {'index': 3, 'action': 'compose a clear answer with confidence caveats'},
        ],
    },
]

agent = Agent(
    name='General Memory Agent',
    system_prompt='...',
    api_key='...',
    memory_config=memory_config,
    memory_skills=BOUND_SKILLS,
)
```

## Attaching resources

Agents can be bound to documents (PDFs, transcripts, images, video) at
construction time. The runtime registers them, hashes them so re-attaching
is a no-op, and makes them retrievable when relevant:

```python
agent = Agent(
    name='Product Analyst',
    system_prompt='You know the product deeply. Answer from the attached docs.',
    api_key='...',
    memory_resources=[
        {'path': 'docs/product_overview.pdf', 'name': 'Product Overview'},
        {'path': 'docs/user_research_2025.pdf', 'name': 'User Research 2025'},
    ],
    memory_config=memory_config,
)
```

Content-hashed: re-running with the same files doesn't re-upload, and
changing a file's content automatically supersedes the prior version.

## Inspecting memory lifecycle events

If you want to see what memory is doing under the hood, stream events and
filter for the memory phases:

```python
events = []
for event in agent.events(messages, thread_id=thread_id):
    if event.get('event') == 'enrichment':
        phase = event.get('payload', {}).get('phase', '')
        if phase.startswith('memory_'):
            events.append(event['payload'])

# Now `events` contains memory_summarize / memory_retrieve / memory_index entries
for e in events:
    print(e['phase'], '-', e.get('message'))
```

## Memory + structured final tool

Final tools work the same way they would without memory — but you can
combine `@depends_on_private_data` and memory retrieval, and the runtime
keeps them straight:

```python
@agent.toolify(name='operations_brief', final_tool=True)
class OperationsBrief(BaseModel):
    summary: str
    priorities: list[str]
    deadlines: list[str]
    risk_flags: list[str]
```

The agent's memory layer surfaces the seeded context; the final tool
captures the recalled answer in a structured shape.

## What's next

- **[Memory & Recall guide](../guides/memory-and-recall.md)** — the deeper
  treatment of skills, insights, retrieval policies, and the asset
  lifecycle.
- **[Swarms](./swarms.md)** — each agent in a swarm can have its own bound
  resources for specialist roles.
- **[Private Data](./private-data.md)** — combining memory with
  redaction-safe execution.
