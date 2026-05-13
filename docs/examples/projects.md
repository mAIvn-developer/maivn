# Real-World Projects

Longer end-to-end examples that demonstrate the SDK at the scale of an
actual feature. Each project is a complete agent or swarm with non-trivial
schemas, multi-step dependencies, and realistic tool integration.

These are more "study these" than "copy these" — they show patterns that
are awkward to demonstrate in a single-page example.

## Automobile specification

**What it shows:** complex hierarchical Pydantic models, multi-system
integration (6-7 vehicle systems), job-level decomposition for parallel
execution.

A single agent designs a complete vehicle spec by populating one
hierarchical Pydantic model. Each top-level system (powertrain, chassis,
electrical, body/aero, interior, safety, climate) becomes an independent
sub-problem the runtime can plan and parallelize:

```python
from pydantic import BaseModel, Field

class PowertrainSystem(BaseModel):
    engine: EngineSpec
    fuel: FuelSpec
    exhaust: ExhaustSpec
    drivetrain: DrivetrainSpec

class ChassisSystem(BaseModel):
    frame: FrameSpec
    suspension: SuspensionSpec
    steering: SteeringSpec
    brakes: BrakeSpec
    wheels: WheelSpec

# ... 5 more systems ...

@agent.toolify(name='build_automobile_spec', final_tool=True)
class AutomobileSpecification(BaseModel):
    name: str
    powertrain: PowertrainSystem
    chassis: ChassisSystem
    electrical: ElectricalSystem
    body_aero: BodyAeroSystem
    interior: InteriorSystem
    safety: SafetySystem
    climate: ClimateSystem
```

The agent decomposes the model construction across independent jobs
behind the scenes; each system can be specified concurrently, then merged
into the final result. Useful when you have a "big structured answer"
problem and want it to finish in `O(depth)` LLM calls instead of
`O(width × depth)`.

**Best for:** product specification, configuration generation, structured
report assembly.

## Data harmonization

**What it shows:** mixing model tools (for LLM-driven harmonization) with
function tools (for deterministic numeric work).

Input: CSV rows with inconsistent date formats (`2025-03-12`, `03/12/2025`,
`Mar 12 '25`) and freeform amounts (`$1,234.56`, `1234.56 USD`, `~1.2k`).

Approach:

- A **Pydantic tool** named `HarmonizedDataset` asks the LLM to interpret
  date and amount strings and produce normalized representations.
- **Function tools** then format the normalized values deterministically
  (no LLM step) — the LLM understands intent, your code handles precision.
- A final tool aggregates everything with summary statistics.

```python
@agent.toolify(name='harmonize_records')
class HarmonizedDataset(BaseModel):
    """LLM converts heterogeneous inputs into a canonical schema."""
    records: list[CanonicalRecord]

@agent.toolify(name='format_amounts')
def format_amounts(records: list[CanonicalRecord]) -> list[CanonicalRecord]:
    """Format decimal amounts with deterministic precision."""
    for r in records:
        r.amount = round(r.amount, 2)
    return records

@agent.toolify(name='final_report', final_tool=True)
class HarmonizationReport(BaseModel):
    records: list[CanonicalRecord]
    summary: SummaryStats
```

**Best for:** ETL workflows where you want the LLM to handle the messy
interpretation step and deterministic code to handle the precision-critical
formatting step.

## HTML email builder

**What it shows:** generating structured HTML with inline CSS via deeply
nested Pydantic models, with private-data injection for branding.

The model represents an email as a tree of components (header, hero,
feature cards, CTA, footer) — each with its own schema for styling:

```python
class FeatureCard(BaseModel):
    title: str
    body: str
    icon: str

class HeroSection(BaseModel):
    headline: str
    subheading: str
    background_color: str = '#1a1a2e'

class HTMLEmail(BaseModel):
    subject: str
    hero: HeroSection
    feature_cards: list[FeatureCard]
    cta_text: str
    cta_url: str
    brand_color: str
    footer_text: str
```

The agent populates the tree based on the user prompt ("Create a product
launch email for CloudSync Pro with feature highlights and pricing"), and
a final tool renders the tree to HTML with inline CSS.

Variants in the same project:
- **Product launch email** — marketing template.
- **Weekly newsletter** — multi-section template with video cards.

**Best for:** templated content generation where the "shape" is fixed but
the content varies. Substitute HTML with Markdown, PDF, structured logs,
or any other format that can be assembled from typed parts.

## Financial planner (MCP swarm)

**What it shows:** a multi-agent swarm with MCP servers for live market
data, private-data injection for API keys, and a final synthesis agent.

Architecture:

```text
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Equity Analyst  │    │ Crypto Analyst  │    │   FX Analyst    │
│  (MCP: fetch)   │    │  (MCP: fetch)   │    │  (MCP: fetch)   │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                ▼
                  ┌─────────────────────────────┐
                  │   Financial Planner         │
                  │   (use_as_final_output)     │
                  └─────────────────────────────┘
```

Each analyst is its own agent with a single MCP server registered for its
asset class. The planner synthesizes their findings into a portfolio
recommendation.

The data URLs (Alpha Vantage, CryptoCompare, Frankfurter) are kept in
`agent.private_data` so they never appear in the LLM context as
hardcoded strings:

```python
private_data = {
    'alpha_url': f'https://www.alphavantage.co/query?...&apikey={alpha_key}',
    'crypto_url': 'https://min-api.cryptocompare.com/data/pricemulti?...',
    'fx_url': 'https://api.frankfurter.app/latest?...',
}

equity_analyst = Agent(name='Equity Analyst', private_data=private_data, ...)
equity_analyst.register_mcp_servers([fetch_server])
# ... same for crypto_analyst and fx_analyst ...

planner_swarm = Swarm(
    name='Financial Planner Swarm',
    agents=[equity_analyst, crypto_analyst, fx_analyst, planner],
)
```

**Best for:** swarms where each member needs distinct external data
sources, and you want a single coordinator agent to merge results.

## Patterns that show up across all four

A few patterns repeat across these projects — worth internalizing:

1. **Make the schema do the work.** When the final answer is structured,
   write the Pydantic model first, then let the agent figure out how to
   fill it in. Don't try to coax structure out of free-form text.

2. **Separate interpretation from precision.** Model tools (Pydantic) are
   great for "understand what the user means." Function tools are great
   for "format this to two decimal places, exactly." Combine them — don't
   force one to do the other's job.

3. **Keep sensitive values out of the LLM.** API keys, customer IDs, and
   URLs with credentials all belong in `agent.private_data`, injected via
   `@depends_on_private_data`. Never inline them in system prompts.

4. **Use swarms for role separation, not task decomposition.** A swarm is
   useful when each member has a distinctly different role (analyst,
   reviewer, director). For "do this big thing in parallel," a single
   agent with deep dependencies is usually simpler.

## What's next

- **[Basics](./basics.md)** — the patterns above, in their simplest form.
- **[Swarms](./swarms.md)** — the multi-agent pieces of the financial
  planner.
- **[MCP Integration](./mcp.md)** — registering MCP servers like the
  ones in the planner.
- **[Memory](./memory.md)** — for projects where the agent needs to
  remember context across invocations.
