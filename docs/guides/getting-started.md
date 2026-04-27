# Getting Started

This guide walks you through creating your first maivn agent with tools.

## Prerequisites

- Python 3.10+
- A maivn API key

## Installation

```bash
pip install maivn
```

Or with uv:

```bash
uv add maivn
```

To install the public Studio companion and enable `maivn studio` from a normal shell:

```bash
pip install maivn-studio
```

## Step 1: Set Up Your API Key

Set your API key as an environment variable:

```bash
export MAIVN_API_KEY=your-api-key
```

Or pass it directly when creating an agent (less recommended for production).

## Step 2: Create Your First Agent

```python
from maivn import Agent
from maivn.messages import HumanMessage

# Create an agent
agent = Agent(
    name='my_first_agent',
    description='A helpful assistant',
    system_prompt='You are a helpful assistant that provides clear, concise answers.',
    api_key='your-api-key',  # Or use MAIVN_API_KEY env var
)
```

## Step 3: Add a Tool

Tools are functions that the agent can call. Use the `@agent.toolify()` decorator:

```python
@agent.toolify(description='Get current weather for a city')
def get_weather(city: str) -> dict:
    # In a real app, call a weather API
    return {'city': city, 'temp': 72, 'condition': 'sunny'}
```

The agent now has access to this tool and can call it when appropriate.

**Tip:** You can also use a docstring instead of the `description` argument:

```python
@agent.toolify()
def get_weather(city: str) -> dict:
    """Get current weather for a city."""
    return {'city': city, 'temp': 72, 'condition': 'sunny'}
```

You can register tools without decorators when that better fits your module layout:

```python
def get_weather(city: str) -> dict:
    """Get current weather for a city."""
    return {'city': city, 'temp': 72, 'condition': 'sunny'}

agent = Agent(
    name='weather_agent',
    api_key='your-api-key',
    tools=[get_weather],
)

# Or add it after construction.
agent.add_tool(get_weather)
```

**Note:** Your tool code executes locally in your environment - it is never transferred to or executed on maivn servers. Only the tool schema (name, description, parameters) is sent to the server for orchestration.

## Step 4: Invoke the Agent

```python
# Send a message to the agent
response = agent.invoke([
    HumanMessage(content='What is the weather in Austin?')
])

print(response.content)
```

## Complete Example

Here's the full working example:

```python
from maivn import Agent
from maivn.messages import HumanMessage

# Create agent
agent = Agent(
    name='weather_agent',
    description='An agent that can check the weather',
    system_prompt='You are a helpful assistant that provides weather information.',
    api_key='your-api-key',
)

# Add a tool
@agent.toolify(description='Get current weather for a city')
def get_weather(city: str) -> dict:
    # In a real app, call a weather API
    return {'city': city, 'temp': 72, 'condition': 'sunny'}

# Invoke the agent
response = agent.invoke([
    HumanMessage(content='What is the weather in Austin?')
])

print(response.content)
```

## Step 5: Add Structured Output

For predictable, typed responses, use a Pydantic model as a final tool:

```python
from pydantic import BaseModel, Field

@agent.toolify(final_tool=True)
class WeatherReport(BaseModel):
    """Structured weather report."""
    city: str = Field(..., description='City name')
    temperature: int = Field(..., description='Temperature in Fahrenheit')
    summary: str = Field(..., description='Human-readable weather summary')

# Force the structured output
response = agent.invoke(
    [HumanMessage(content='Weather report for Austin')],
    force_final_tool=True,
)

# Response contains structured data matching WeatherReport
print(response.content)
```

## Step 6: Inspect Execution Events

For live progress and enrichment/tool events, use the event builder:

```python
response = agent.events().invoke(
    [HumanMessage(content='What is the weather in Austin?')],
)
```

If you are sending execution events to a browser frontend, use `EventBridge` and pick the audience explicitly:

```python
from maivn.events import EventBridge

# End-user frontend
public_bridge = EventBridge("session-1", audience="frontend_safe")

# Internal developer/admin tooling
internal_bridge = EventBridge("session-1", audience="internal")
```

Use `frontend_safe` for customer-facing browser sessions. Use `internal` for trusted tools such as mAIvn Studio, Booth, or your own internal debug consoles.

## Built-in Capabilities

The maivn system has several built-in capabilities that don't require custom tools:

- **Datetime awareness** - Agents automatically know the current date and time
- **Web search** - Search for current information (server-side)
- **Code execution** - Run Python in a sandbox (server-side)

See [System Tools Guide](system-tools.md) for details.

## Next Steps

Now that you have a working agent, explore these topics:

- [Tools Guide](tools.md) - Learn about different tool types
- [Dependencies Guide](dependencies.md) - Chain tools together
- [Structured Output Guide](structured-output.md) - Guaranteed typed responses
- [Multi-Agent Guide](multi-agent.md) - Coordinate multiple agents
- [System Tools Guide](system-tools.md) - Built-in server capabilities
- [Memory and Recall Guide](memory-and-recall.md) - Summarize, retrieve, and index context across turns
- [mAIvn Studio Guide](maivn-studio.md) - Run demos with UI + API and inspect live event streams
- [Studio Authoring and Debugging](maivn-studio-authoring-and-debugging.md) - Make demos Studio-ready
- [Frontend Event Bridges](frontend-event-bridges.md) - Stream safe app-facing events to browsers

## Troubleshooting

### "Agent requires either a Client instance or an api_key"

Make sure you provide either `api_key` to the Agent constructor or set the `MAIVN_API_KEY` environment variable.

### Tools not being called

Check that your tool has a clear description. The LLM uses the description to decide when to call the tool.

### Connection errors

Verify the maivn server is running and accessible. Check your network configuration.

See [Troubleshooting](../troubleshooting.md) for more help.
