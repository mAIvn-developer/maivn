# Quickstart

By the end of this guide you'll have a running maivn agent: it takes a plain-English question, decides on its own to call a tool you wrote, and hands you back both a readable answer and a typed, structured result your code can rely on. No servers to wire up by hand and no prompt-engineering rabbit holes — just a few lines of Python.

## What You'll Build

You'll create an agent, give it a `get_weather` tool, ask it a question in natural language, and read the response. Then you'll layer on two things production apps want: a guaranteed typed result (so you can `report.temperature` instead of parsing text) and live progress events (so a UI can show what the agent is doing while it works).

The agent does the deciding. You describe what each tool does; the agent figures out when to call it and how to combine the results into an answer.

## Prerequisites

- Python 3.10+
- A [mAIvn Developer Portal](https://developer.maivn.io) account
- A mAIvn API key (the steps below show where to create one)

## Step 1: Create an Isolated Python Environment

Use one of these paths. Both create a project-local `.venv` so mAIvn and its
dependencies do not change your system Python.

### Option A: Python `venv` and pip

Create a working directory and virtual environment:

```bash
mkdir maivn-quickstart
cd maivn-quickstart
python -m venv .venv
```

Activate it in Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or activate it on macOS/Linux:

```bash
source .venv/bin/activate
```

Then upgrade pip and install the SDK:

```bash
python -m pip install --upgrade pip
python -m pip install maivn
```

Keep the environment active while following the guide. Run `deactivate` when
you are finished.

### Option B: uv-managed virtual environment

From an empty working directory, create `.venv` with Python 3.12 and install the
SDK:

```bash
mkdir maivn-quickstart
cd maivn-quickstart
uv venv --python 3.12
uv pip install maivn
```

For this pip-compatible uv workflow, activate the environment before running
commands: use `.venv\Scripts\Activate.ps1` in Windows PowerShell or
`source .venv/bin/activate` on macOS/Linux.

For a longer-lived project, use `uv init`, `uv add maivn`, and
`uv run python app.py`. That workflow records the dependency in
`pyproject.toml`, keeps the project environment synchronized, and does not
require activation.

### Optional: install mAIvn Studio

Install the public Studio companion into the same environment:

```bash
python -m pip install maivn-studio
maivn studio
```

With uv, use:

```bash
uv pip install maivn-studio
maivn studio
```

If you chose the project workflow instead, use `uv add maivn-studio` and
`uv run maivn studio`.

## Step 2: Create and Set Up Your API Key

1. Sign in to the [mAIvn Developer Portal](https://developer.maivn.io).
2. Open [API Keys](https://developer.maivn.io/projects/current/api-keys). If
   this is your first visit, the portal creates a default organization and
   project so you can continue without additional workspace setup.
3. Select **Create Key**, enter a descriptive name, and choose the narrowest
   permission scope your application needs.
4. Copy the key immediately and store it securely. The portal shows the full
   value only once.

Set your API key as an environment variable:

Windows PowerShell:

```powershell
$env:MAIVN_API_KEY="your-api-key"
```

macOS/Linux:

```bash
export MAIVN_API_KEY=your-api-key
```

The `Agent` constructor does not read environment variables automatically; read
the key explicitly or pass a preconfigured `Client`.

## Step 3: Create Your First Agent

Create `app.py`:

```python
import os

from maivn import Agent
from maivn.messages import HumanMessage

# Create an agent
agent = Agent(
    name='my_first_agent',
    description='A helpful assistant',
    system_prompt='You are a helpful assistant that provides clear, concise answers.',
    api_key=os.environ['MAIVN_API_KEY'],
)
```

## Step 4: Add a Tool

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
    api_key=os.environ['MAIVN_API_KEY'],
    tools=[get_weather],
)

# Or add it after construction.
agent.add_tool(get_weather)
```

**Note:** Your tool code executes locally in your environment - it is never transferred to or executed on mAIvn's servers. Only the tool schema (name, description, parameters) is sent to the hosted orchestrator.

## Step 5: Invoke the Agent

```python
# Send a message to the agent
response = agent.invoke([
    HumanMessage(content='What is the weather in Austin?')
])

print(response.response)
```

`response.response` holds the final assistant text — the natural-language answer the agent produced after deciding whether to call your tool.

Run the script from the same shell where `MAIVN_API_KEY` is set:

```bash
python app.py
```

Or, from a uv project initialized with `uv init`:

```bash
uv run python app.py
```

## Complete Example

Here's the full working example:

```python
import os

from maivn import Agent
from maivn.messages import HumanMessage

# Create agent
agent = Agent(
    name='weather_agent',
    description='An agent that can check the weather',
    system_prompt='You are a helpful assistant that provides weather information.',
    api_key=os.environ['MAIVN_API_KEY'],
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

print(response.response)
```

## Step 6: Add Structured Output

When your code needs to act on the answer — not just print it — you want a guaranteed shape, not prose to parse. Mark a Pydantic model as a final tool and the agent will fill it in:

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

# response.result is a WeatherReport instance
print(response.result)
print(response.result.temperature)
```

`final_tool=True` designates the Pydantic model as the structured-output tool, and `force_final_tool=True` tells the agent to finish by populating it. The typed value lands on `response.result`, while `response.response` still carries any accompanying text.

For a deeper look — including the faster `agent.structured_output(MyModel).invoke(...)` builder for one-shot extraction — see the [Structured Output Guide](structured-output.md).

## Step 7: Stream Live Progress

For a responsive UI, you'll want to show what the agent is doing while it works, instead of waiting for the whole turn to finish. The events builder wraps `invoke()` and reports progress as it happens:

```python
response = agent.events().invoke(
    [HumanMessage(content='What is the weather in Austin?')],
)
```

Pass an `on_event` callback to react to each event as it arrives, or filter with `include`/`exclude`:

```python
def handle(event: dict) -> None:
    print(event)

response = agent.events(on_event=handle).invoke(
    [HumanMessage(content='What is the weather in Austin?')],
)
```

If you'd rather pull events yourself, iterate `agent.stream(...)`, which yields events one at a time as the agent executes:

```python
for event in agent.stream([HumanMessage(content='What is the weather in Austin?')]):
    print(event)
```

Between tool calls and the final answer, the agent emits fixed-label phase indicators — things like evaluating the request, planning actions, and synthesizing the response — so a frontend can render a live status indicator without guessing. Status messages are a separate, opt-in channel you can turn on with `agent.stream(..., status_messages=True)`.

To send these events to a browser frontend, see the [Frontend Events](frontend-events.md) guide, which covers the one-line backend mount and client examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python, Rust, .NET, and more.

## Built-in Capabilities

The mAIvn runtime has several built-in capabilities that don't require custom tools:

- **Datetime awareness** - Agents automatically know the current date and time
- **Web search** - Search for current information (runs within the mAIvn runtime)
- **Code execution** - Run Python in a sandbox (runs within the mAIvn runtime)

See [System Tools Guide](system-tools.md) for details.

## Next Steps

You now have an agent that calls a tool, returns a typed result, and streams progress. From here, the [Examples](../examples/README.md) tour is the fastest way to see the rest of the SDK in working code. A few core concepts to build on next:

- [Core Concepts](../core-concepts.md) - How agents, tools, and responses fit together
- [Tools Guide](tools.md) - Tool types, schemas, and registration patterns
- [Structured Output Guide](structured-output.md) - Guaranteed typed responses
- [Multi-Agent Guide](multi-agent.md) - Coordinate multiple specialized agents with Swarms

## Troubleshooting

### "Agent requires either a Client instance or an api_key"

Make sure you provide either `api_key` or `client` to the `Agent` constructor.
If you store the key in `MAIVN_API_KEY`, read it explicitly with
`os.environ['MAIVN_API_KEY']` or construct a client with `ClientBuilder.from_environment()`.

### Tools not being called

Check that your tool has a clear description. The LLM uses the description to decide when to call the tool.

### Connection errors

Verify your network can reach the hosted mAIvn orchestrator and that your API key is valid. Check your network configuration and any outbound proxy or firewall rules.

See [Troubleshooting](../troubleshooting.md) for more help.
