# Swarms

Multiple agents working together. A `Swarm` holds a list of `Agent`s and
routes work between them — the user message goes in, the result of the
designated final-output agent comes out.

## A three-agent swarm

A realistic investment-analysis swarm: a financial analyst, a risk assessor,
and a director who synthesizes their findings into a final memo.

```python
from maivn import Agent, Swarm
from maivn.messages import HumanMessage
from pydantic import BaseModel, Field

# Specialist 1: financial analysis
financial_analyst = Agent(
    name='Financial Analyst',
    system_prompt='Analyze financial data (revenue, EBITDA, margins). Focus on numerical trends.',
    api_key='...',
)

@financial_analyst.toolify(name='calculate_cagr')
def calculate_cagr(start_value: float, end_value: float, periods: int) -> float:
    """Compound Annual Growth Rate."""
    return (end_value / start_value) ** (1 / periods) - 1

# Specialist 2: risk assessment
risk_assessor = Agent(
    name='Risk Assessor',
    system_prompt='Evaluate market volatility, competition, and regulatory risk. Score 1-10.',
    api_key='...',
)

@risk_assessor.toolify(name='calculate_risk_score')
def calculate_risk_score(volatility: float, competition: float, regulation: float) -> float:
    return round(volatility * 0.4 + competition * 0.3 + regulation * 0.3, 1)

# Final output: investment director
investment_director = Agent(
    name='Investment Director',
    use_as_final_output=True,
    system_prompt=(
        'Synthesize the Financial Analyst and Risk Assessor reports into an '
        'Investment Memo. Always call generate_investment_memo as the final tool.'
    ),
    api_key='...',
)

@investment_director.toolify(name='generate_investment_memo', final_tool=True)
class InvestmentMemo(BaseModel):
    company_name: str
    financial_data: dict
    risk_data: dict
    recommendation: str  # Buy / Hold / Sell

swarm = Swarm(
    name='Investment Analysis Swarm',
    description='Comprehensive company evaluation and investment decisioning.',
    agents=[financial_analyst, risk_assessor, investment_director],
)

swarm.invoke(
    [HumanMessage(content=(
        'Evaluate "TechNova Solutions" for a $50M Series C investment. '
        'Revenue: [12.5, 18.2, 25.0, 38.5]M USD over 4 years. '
        'Net income: [1.2, 2.5, 4.8, 8.2]M USD. Provide a memo.'
    ))],
    force_final_tool=True,
)
```

A few things worth noticing:

- `use_as_final_output=True` on `investment_director` tells the swarm that
  this agent's output is what the swarm returns.
- `final_tool=True` on `generate_investment_memo` means the agent must
  terminate by calling that tool.
- `force_final_tool=True` at the swarm level requires that path on every
  invocation — no free-form answers slip through.

## Designating the final-output agent

A swarm needs to know which agent's output to return. Three ways:

1. **`use_as_final_output=True` on one agent** (most explicit):

   ```python
   director = Agent(..., use_as_final_output=True)
   ```

2. **The agent owns the only `final_tool`** — the swarm infers it.

3. **Let the swarm decide** — no `use_as_final_output` and no
   `force_final_tool`. The orchestrator picks the most relevant agent based
   on the task. Useful for exploratory swarms.

## Per-agent `final_tool` ownership

Multiple agents can each own their own `final_tool` — the runtime keeps
them straight. Mark one agent `use_as_final_output=True` to disambiguate.

```python
@category_classifier.toolify(name='emit_category', final_tool=True)
class CategoryDecision(BaseModel):
    ticket_id: str
    product_area: str

@priority_scorer.toolify(name='emit_priority', final_tool=True)
class PriorityDecision(BaseModel):
    ticket_id: str
    priority: str  # P0 / P1 / P2 / P3

resolution_director = Agent(
    name='Resolution Director',
    use_as_final_output=True,   # this one's verdict is the swarm's answer
    system_prompt='Consume category + priority, then call emit_triage_decision.',
    api_key='...',
)

@resolution_director.toolify(name='emit_triage_decision', final_tool=True)
class TriageDecision(BaseModel):
    ticket_id: str
    product_area: str
    priority: str
    assigned_team: str
    next_action: str

triage_swarm = Swarm(
    name='Support Ticket Triage Swarm',
    agents=[category_classifier, priority_scorer, resolution_director],
)
```

If you forget to designate one final agent and multiple own a `final_tool`,
`swarm.validate_tool_configuration()` raises so the misconfiguration
surfaces before you invoke.

## `@swarm.member` — factory-style registration

Sometimes it's cleaner to build the agent inside a factory that gets
registered with the swarm. The `@swarm.member` decorator runs the factory
and threads agent dependencies through it:

```python
swarm = Swarm(
    name='Member Planning Swarm',
    system_prompt='Coordinate the member agents. Editor produces the final brief.',
)

@swarm.toolify(name='load_launch_context')
def load_launch_context(market: str = 'healthcare') -> dict:
    return {'market': market, 'launch_window': 'Q3', 'constraints': ['HIPAA review']}

@swarm.member
@depends_on_tool(load_launch_context, arg_name='launch_context')
def research_agent() -> Agent:
    agent = Agent(name='Member Research Agent', system_prompt='...', api_key='...')

    @agent.toolify(name='compile_market_notes')
    def compile_market_notes() -> dict:
        return {'buyer': 'clinic operations leaders', 'risk': 'security review timing'}

    return agent
```

The strategy editor depends on the research agent's output:

```python
strategy_editor = Agent(
    name='Member Strategy Editor',
    system_prompt='Use write_launch_brief as the final tool.',
    use_as_final_output=True,
    api_key='...',
)

@strategy_editor.toolify(name='write_launch_brief', final_tool=True)
class LaunchBrief(BaseModel):
    title: str
    market: str
    research_notes: dict     # filled in from the research agent
    recommended_motion: str
    next_steps: list[str]

# Register the editor with a required dependency on research_agent
strategy_editor = swarm.member.depends_on_agent(
    research_agent, arg_name='research_notes',
)(strategy_editor)
```

When the editor's final tool is built, the runtime ensures the research
agent has run and supplies its result as `research_notes`.

## Swarm-level tools

Tools attached directly to the swarm are visible to every agent inside it.
Useful for shared context loaders, audit logs, or anything that should
"feel like part of the room":

```python
@swarm.toolify(name='load_account_context')
def load_account_context(account_id: str) -> dict:
    return {'tier': 'enterprise', 'mrr': 12000, 'csat': 4.6}
```

Any agent in the swarm can call this tool by name.

## Scaling up: bound resources per agent

For larger swarms, each agent can be bound to its own set of memory
resources (PDFs, transcripts, internal docs). The runtime makes each
agent's resources retrievable to it without leaking to the others — useful
when you want specialists with distinct knowledge bases:

```python
brand_strategist = Agent(
    name='Brand Strategist',
    system_prompt='You know the brand strategy deeply. Answer from BRAND.pdf.',
    api_key='...',
    memory_resources=[{'path': 'business/BRAND.pdf', 'name': 'Brand Strategy'}],
)

valuation_analyst = Agent(
    name='Valuation Analyst',
    system_prompt='You know the financial model deeply. Answer from VALUATION.pdf.',
    api_key='...',
    memory_resources=[{'path': 'business/VALUATION.pdf', 'name': 'Valuation Model'}],
)

bi_swarm = Swarm(
    name='Business Intelligence Swarm',
    agents=[brand_strategist, valuation_analyst, ...],  # plus 5 more specialists
)
```

See [Memory](./memory.md) for resource lifecycle, retrieval policies, and
configuration.

## Async, streaming, batch

Everything that works on `Agent` works on `Swarm`:

```python
swarm.invoke(messages)
swarm.stream(messages)        # token-level streaming
await swarm.ainvoke(messages)
await swarm.astream(messages)
swarm.batch([msgs_1, msgs_2, msgs_3])  # multiple invocations concurrently
```

See [Batch & Scheduling](./batch-and-scheduling.md) for batch semantics.

## What's next

- **[Multi-Agent guide](../guides/multi-agent.md)** — the deeper treatment of
  swarm orchestration, including final-output policy and resource binding.
- **[Memory](./memory.md)** — bound resources per agent, retrieval, and
  skills/insights.
- **[Agents & Tools](./agents-and-tools.md)** — hook execution modes
  including swarm-wide hooks.
