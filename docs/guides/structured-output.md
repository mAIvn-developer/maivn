# Structured Output Guide

Get guaranteed, typed responses from your agents using structured output patterns.

## Overview

The maivn SDK provides two approaches for structured output:

| Approach | When to Use | Orchestration |
|----------|-------------|---------------|
| `.structured_output()` builder | When orchestration overhead is not needed | **Bypassed** (faster) |
| `final_tool` + `force_final_tool` | Multi-step workflows needing full planning | Full orchestration |

Both approaches support tool execution. The key difference is whether the orchestration layer is involved.

## Quick Comparison

```python
# Approach 1: .structured_output() builder - bypasses orchestrator
response = agent.structured_output(SentimentAnalysis).invoke(
    [HumanMessage(content='Analyze this text')]
)

# Approach 2: final_tool pattern - full orchestration
response = agent.invoke(
    [HumanMessage(content='Fetch data and create report')],
    force_final_tool=True,
)
```

---

## Approach 1: `.structured_output()` Builder (Fast Path)

Use this when you want faster responses by skipping the orchestration layer.

### How It Works

The `.structured_output()` builder:
1. **Bypasses the orchestrator** - routes directly to the assignment agent
2. **Tools still execute** - registered tools are available and will run as needed
3. **Faster responses** - reduced latency by skipping orchestrator overhead

### Basic Usage

```python
from pydantic import BaseModel, Field
from maivn import Agent
from maivn.messages import HumanMessage

class SentimentAnalysis(BaseModel):
    """Sentiment analysis result."""
    sentiment: str = Field(..., description='positive, negative, or neutral')
    confidence: float = Field(..., ge=0, le=1, description='Confidence score')
    key_phrases: list[str] = Field(..., description='Important phrases')

agent = Agent(name='analyzer', api_key='...')

# Fast structured output - bypasses orchestrator
response = agent.structured_output(SentimentAnalysis).invoke(
    [HumanMessage(content='Analyze: "I absolutely love this product!"')]
)
```

### Builder Options

The `.structured_output()` builder returns an invocation builder with these options:

```python
response = agent.structured_output(MyModel).invoke(
    messages=[HumanMessage(content='...')],
    model='fast',        # 'fast', 'balanced', or 'max'
    reasoning='minimal', # 'minimal', 'low', 'medium', 'high'
    thread_id='...',     # For multi-turn conversations
)
```

### When to Use

- **Simple workflows**: When orchestration overhead is not needed
- **Direct extraction**: Text extraction, classification, summarization
- **Performance-sensitive**: When response latency matters
- **Single-agent tasks**: When you don't need Swarm coordination

### Limitations

- **No Swarm support**: Cannot use `.structured_output()` with Swarms
- **No orchestration**: Complex multi-step planning is skipped

---

## Approach 2: `final_tool` Pattern (Full Orchestration)

Use this when your workflow requires tool execution before generating structured output.

### How It Works

1. Mark a Pydantic model as `final_tool=True`
2. Invoke with `force_final_tool=True`
3. The orchestrator executes tools as needed, then calls the final tool

### Basic Usage

```python
from pydantic import BaseModel, Field
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(
    name='report_agent',
    api_key='...',
    system_prompt='Generate detailed reports.',
)

@agent.toolify(final_tool=True)
class Report(BaseModel):
    """A structured report."""
    title: str = Field(..., description='Report title')
    summary: str = Field(..., description='Executive summary')
    findings: list[str] = Field(..., description='Key findings')
    recommendation: str = Field(..., description='Primary recommendation')

response = agent.invoke(
    [HumanMessage(content='Analyze Q4 sales data')],
    force_final_tool=True,
)
```

You can also register the final model imperatively:

```python
agent.add_tool(Report, name='report', final_tool=True)
```

## Model Requirements

### Use Descriptive Fields

The LLM uses field descriptions to populate values:

```python
@agent.toolify(final_tool=True)
class UserProfile(BaseModel):
    name: str = Field(..., description='Full name of the user')
    email: str = Field(..., description='Email address')
    role: str = Field(..., description='Job role or title')
    department: str = Field(..., description='Department name')
```

### Support Complex Types

```python
from typing import Literal
from datetime import datetime

@agent.toolify(final_tool=True)
class DetailedReport(BaseModel):
    title: str
    status: Literal['draft', 'review', 'final']
    created_at: datetime
    tags: list[str]
    metrics: dict[str, float]
    priority: int = Field(..., ge=1, le=5)  # 1-5 validation
```

### Nested Models

```python
class Author(BaseModel):
    name: str
    email: str

class Section(BaseModel):
    title: str
    content: str

@agent.toolify(final_tool=True)
class Document(BaseModel):
    title: str
    author: Author
    sections: list[Section]
    word_count: int
```

### force_final_tool

Guarantees the final tool is used:

```python
response = agent.invoke(
    [HumanMessage(content='Create a report')],
    force_final_tool=True,
)
```

### When to Use final_tool Pattern

- **Tool workflows**: When tools must execute before generating output
- **Data fetching**: Fetch data with tools, then structure the result
- **Multi-agent**: Swarm workflows that need structured final output
- **Dependencies**: When final output depends on tool results

## Validation

### Only One Final Tool Per Scope

Each scope (a single agent, or the swarm itself) can have at most ONE tool
marked `final_tool=True`:

```python
# This will raise an error
@agent.toolify(final_tool=True)
class Report(BaseModel): ...

@agent.toolify(final_tool=True)  # Error!
class Summary(BaseModel): ...
```

Error message:
```
TOOL CONFIGURATION ERROR
================================================================================
[ERROR] Multiple tools marked with final_tool=True: 'Report', 'Summary'
  SCOPE: Agent 'my_agent'
  ISSUE: Only ONE tool per scope can be designated as the final output tool.
  FIX: Remove 'final_tool=True' from all but one tool in this scope.
================================================================================
```

Inside a swarm, each agent has its own scope, so different agents may each
own a distinct `final_tool`. When more than one scope in the swarm declares
a `final_tool`, the swarm must designate which one produces its final
response by setting `use_as_final_output=True` on exactly one agent.

### Mixing always_execute and final_tool

`always_execute` and `final_tool` are orthogonal — one controls *frequency*,
the other controls *role*. They can coexist on the same tool or on different
tools in the same scope:

```python
# Valid: an audit tool always runs, and a separate tool finalizes output.
@agent.toolify(always_execute=True)
def logger() -> dict: ...

@agent.toolify(final_tool=True)
class Report(BaseModel): ...
```

### force_final_tool Requires a Final Tool

```python
agent = Agent(name='test', api_key='...')

@agent.toolify()  # No final_tool
def my_tool() -> dict: ...

# This raises an error
response = agent.invoke(
    [...],
    force_final_tool=True,  # Error: no final tool defined!
)
```

## Combining with Dependencies

Final tools can have dependencies:

```python
@agent.toolify(description='Fetch sales data')
def fetch_sales() -> dict:
    return {'sales': [...]}

@agent.toolify(description='Calculate metrics')
@depends_on_tool(fetch_sales, arg_name='sales_data')
def calculate_metrics(sales_data: dict) -> dict:
    return {'revenue': 100000, 'growth': 15}

@agent.toolify(final_tool=True)
@depends_on_tool(calculate_metrics, arg_name='metrics')
class SalesReport(BaseModel):
    """Final sales report."""
    total_revenue: float = Field(..., description='Total revenue')
    growth_rate: float = Field(..., description='Growth percentage')
    summary: str = Field(..., description='Executive summary')
```

The dependency chain executes in order, then the final tool produces structured output.

## Swarm Final Output

In a swarm, you can either:

### Option 1: Swarm-Level Final Tool

```python
swarm = Swarm(name='team', agents=[agent1, agent2])

@swarm.toolify(final_tool=True)
class TeamReport(BaseModel):
    summary: str
    contributions: list[str]
```

### Option 2: Final Output Agent

```python
writer = Agent(
    name='writer',
    api_key='...',
    use_as_final_output=True,  # This agent produces final output
)

swarm = Swarm(name='team', agents=[researcher, writer])

response = swarm.invoke(
    [...],
    force_final_tool=True,
)
```

Only ONE of these can be used per swarm.

## Best Practices

### 1. Descriptive Field Descriptions

```python
# Good
summary: str = Field(..., description='A 2-3 sentence executive summary')

# Less helpful
summary: str = Field(...)
```

### 2. Use Appropriate Types

```python
# Good: specific types
score: float = Field(..., ge=0, le=100)
status: Literal['pending', 'complete', 'failed']
tags: list[str]

# Avoid: overly generic
data: dict  # Too vague
```

### 3. Keep Models Focused

```python
# Good: focused model
@agent.toolify(final_tool=True)
class WeatherReport(BaseModel):
    city: str
    temperature: int
    conditions: str

# Avoid: kitchen sink model
@agent.toolify(final_tool=True)
class Everything(BaseModel):
    weather: dict
    news: dict
    stocks: dict
    calendar: dict
```

### 4. Add Model Docstrings

```python
@agent.toolify(final_tool=True)
class AnalysisReport(BaseModel):
    """Comprehensive analysis report with findings and recommendations.

    Used for quarterly business reviews and strategic planning.
    """
    title: str
    findings: list[str]
    recommendation: str
```

## Error Handling

### Missing Required Fields

If the LLM fails to populate a required field, Pydantic validation fails:

```python
@agent.toolify(final_tool=True)
class Strict(BaseModel):
    required_field: str  # Must be provided
    optional_field: str | None = None  # Can be None
```

### Default Values

Provide defaults for optional fields:

```python
@agent.toolify(final_tool=True)
class Flexible(BaseModel):
    title: str  # Required
    tags: list[str] = []  # Defaults to empty list
    priority: int = 5  # Defaults to 5
```

---

## Choosing the Right Approach

### Decision Guide

```
Do you need full orchestration (multi-step planning, Swarm coordination)?
├── YES → Use final_tool + force_final_tool
│         - Full orchestration layer
│         - Works with Swarms
│         - Complex multi-step workflows
│
└── NO → Use .structured_output() builder
          - Bypasses orchestrator
          - Faster response times
          - Tools still execute as needed
```

### Detailed Comparison

| Feature | `.structured_output()` | `final_tool` + `force_final_tool` |
|---------|------------------------|-----------------------------------|
| **Orchestration** | Bypassed (direct assignment) | Full orchestration |
| **Response time** | Faster (skips orchestrator) | Standard |
| **Tool execution** | Tools execute as needed | Tools execute as needed |
| **Swarm support** | **Not supported** | Fully supported |
| **Use case** | Simple workflows, performance-sensitive | Complex multi-step workflows |
| **Model registration** | Inline (not registered as tool) | Registered as `final_tool=True` |

### Example: Same Task, Different Approaches

**Task**: Analyze customer feedback

```python
class FeedbackAnalysis(BaseModel):
    sentiment: str
    topics: list[str]
    urgency: int

# Approach 1: Bypasses orchestrator (faster)
response = agent.structured_output(FeedbackAnalysis).invoke(
    [HumanMessage(content='Analyze: "Your service is terrible..."')]
)

# Approach 2: Full orchestration (for complex workflows)
@agent.toolify(description='Fetch customer feedback from database')
def fetch_feedback(customer_id: str) -> dict:
    return {'feedback': '...'}

@agent.toolify(final_tool=True)
@depends_on_tool(fetch_feedback, arg_name='data')
class FeedbackAnalysis(BaseModel):
    sentiment: str
    topics: list[str]
    urgency: int

response = agent.invoke(
    [HumanMessage(content='Analyze feedback for customer 123')],
    force_final_tool=True,
)
```

## See Also

- [Tools Guide](tools.md) - Model tool basics
- [Agent API](../api/agent.md) - `invoke()` and `structured_output()` methods
- [Swarm API](../api/swarm.md) - Swarm final output
