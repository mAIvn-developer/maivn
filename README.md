# maivn

Python SDK for building agentic systems with typed tools and dependencies.

The maivn SDK provides a clean, declarative interface for creating AI agents with:

- **Typed tool definitions** using decorators and Pydantic models
- **Dependency injection** between tools, agents, and external data
- **Structured outputs** with guaranteed schema conformance
- **Multi-agent orchestration** via Swarms
- **MCP integration** for external tool servers

## Features

- **Declarative Tools**: Define tools with `@agent.toolify()` - functions or Pydantic models
- **Dependency Graph**: Chain tools with `@depends_on_tool`, inject secrets with `@depends_on_private_data`
- **Structured Output**: Use `final_tool=True` for guaranteed typed responses
- **Multi-Agent**: Coordinate agents with `Swarm` and `@depends_on_agent`
- **MCP Support**: Connect external MCP servers (stdio/HTTP) as tool providers
- **Interrupts**: Collect user input mid-execution with `@depends_on_interrupt`
- **System Tools**: Built-in `web_search`, `repl`, `think`

## Requirements

- Python 3.10+

## Installation

```bash
pip install maivn
```

`maivn` depends on the public `maivn-shared` package and will install it automatically
from PyPI.

To install the public Studio companion and enable `maivn studio` from a normal shell:

```bash
pip install "maivn[studio]"
```

If you prefer to install the companion package directly, `pip install maivn-studio` also works.

## Quick Start

### Basic Agent with Tools

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(
    name='weather_agent',
    description='Provides weather information',
    system_prompt='You are a helpful weather assistant.',
    api_key='your-api-key',  # or set MAIVN_API_KEY env var
)

@agent.toolify(description='Get current weather for a city')
def get_weather(city: str) -> dict:
    return {'city': city, 'temp': 72, 'condition': 'sunny'}

response = agent.invoke([HumanMessage(content='What is the weather in Austin?')])
print(response.content)
```

### Fast Structured Output

Use `.structured_output()` to bypass the orchestrator for faster responses:

```python
from pydantic import BaseModel, Field

class SentimentAnalysis(BaseModel):
    sentiment: str = Field(..., description='positive, negative, or neutral')
    confidence: float = Field(..., ge=0, le=1)

# Fast path - bypasses orchestrator, tools still execute as needed
response = agent.structured_output(SentimentAnalysis).invoke(
    [HumanMessage(content='Analyze: "I love this product!"')]
)
```

### Structured Output with Full Orchestration

Use `final_tool` pattern when you need full orchestration for complex workflows:

```python
from pydantic import BaseModel, Field

@agent.toolify(final_tool=True)
class WeatherReport(BaseModel):
    """Structured weather report."""
    city: str = Field(..., description='City name')
    temperature: int = Field(..., description='Temperature in Fahrenheit')
    summary: str = Field(..., description='Human-readable weather summary')

# Full orchestration for complex workflows
response = agent.invoke(
    [HumanMessage(content='Weather report for Austin')],
    force_final_tool=True,
)
```

### Tool Dependencies

```python
from maivn import depends_on_tool

@agent.toolify(description='Fetch raw sensor data')
def fetch_sensor_data(sensor_id: str) -> dict:
    return {'sensor_id': sensor_id, 'readings': [72, 73, 71]}

@agent.toolify(description='Analyze sensor readings')
@depends_on_tool(fetch_sensor_data, arg_name='sensor_data')
def analyze_readings(sensor_data: dict) -> dict:
    readings = sensor_data['readings']
    return {'average': sum(readings) / len(readings)}
```

### Private Data Injection

```python
from maivn import depends_on_private_data

@agent.toolify(description='Call external API')
@depends_on_private_data(data_key='api_secret', arg_name='secret')
def call_api(query: str, secret: str) -> dict:
    # 'secret' is injected server-side, never exposed to LLM
    return {'result': f'Called API with query: {query}'}

# Set private data (stays server-side)
agent.private_data = {'api_secret': 'sk-xxx'}
```

### Multi-Agent with Swarm

```python
from maivn import Agent, Swarm, depends_on_agent

researcher = Agent(
    name='researcher',
    description='Research specialist',
    system_prompt='You research topics thoroughly.',
    api_key='your-api-key',
)

writer = Agent(
    name='writer',
    description='Content writer',
    system_prompt='You write clear, engaging content.',
    api_key='your-api-key',
    use_as_final_output=True,  # This agent produces final output
    included_nested_synthesis='auto',  # default: orchestrator/runtime decides
)

@researcher.toolify(description='Research a topic')
def research_topic(topic: str) -> dict:
    return {'findings': f'Research findings about {topic}'}

@writer.toolify(description='Write article')
@depends_on_agent(researcher, arg_name='research')
def write_article(research: dict) -> dict:
    return {'article': f'Article based on: {research["findings"]}'}

swarm = Swarm(
    name='content_team',
    agents=[researcher, writer],
)

response = swarm.invoke(
    HumanMessage(content='Write about AI agents'),
    force_final_tool=True,
)
```

## Security

**Your code stays local.** All function tools and MCP tools execute in your environment - code is never transferred to or executed on maivn servers.

The maivn server only receives tool schemas (names, descriptions, parameters) and orchestrates execution. The actual tool code runs locally in your environment, ensuring:

- Your business logic remains private
- Sensitive data processed by tools stays local
- You have full control over tool access and permissions

## Scalability

**Thousands of tools, no problem.** The maivn system provides high-performance tool management for agents and swarms with large tool catalogs. Tool selection and orchestration remain fast and accurate regardless of how many tools you register.

## Core Concepts

| Concept          | Description                                              |
| ---------------- | -------------------------------------------------------- |
| **Agent**        | Container for tools, configuration, and invocation logic |
| **Swarm**        | Coordinates multiple agents with shared tool access      |
| **Tool**         | Function or Pydantic model exposed to the LLM            |
| **Dependency**   | Declares data flow between tools/agents                  |
| **Final Tool**   | Marked tool that produces the structured output          |
| **Private Data** | Server-side secrets injected at execution time           |

## Public API

### Core Classes

| Class           | Description                                    |
| --------------- | ---------------------------------------------- |
| `Agent`         | Main agent class with tools and invocation     |
| `Swarm`         | Multi-agent orchestration container            |
| `Client`        | HTTP connection manager (usually auto-created) |
| `ClientBuilder` | Factory for creating Client instances          |
| `BaseScope`     | Base class for Agent/Swarm                     |
| `MCPServer`     | MCP server configuration                       |
| `MCPAutoSetup`  | Auto-setup for uvx-based MCP servers           |

### Decorators

| Decorator                                     | Description                         |
| --------------------------------------------- | ----------------------------------- |
| `@agent.toolify()`                            | Register a function/model as a tool |
| `@depends_on_tool(tool, arg)`                 | Inject output from another tool     |
| `@depends_on_agent(agent, arg)`               | Inject output from another agent    |
| `@depends_on_private_data(key, arg)`          | Inject server-side secret           |
| `@depends_on_interrupt(arg, prompt, handler)` | Collect user input                  |

### Configuration

| Function                                  | Description               |
| ----------------------------------------- | ------------------------- |
| `ConfigurationBuilder.from_environment()` | Load config from env vars |
| `get_configuration()`                     | Get current configuration |
| `MaivnConfiguration`                      | Configuration model       |

### Logging

| Function                      | Description             |
| ----------------------------- | ----------------------- |
| `configure_logging(log_file)` | Initialize SDK logging  |
| `get_logger()`                | Get SDK logger instance |

### Messages

Import from `maivn.messages`:

| Class             | Description                          |
| ----------------- | ------------------------------------ |
| `HumanMessage`    | User input message                   |
| `AIMessage`       | Assistant response                   |
| `SystemMessage`   | System prompt                        |
| `RedactedMessage` | Message with sensitive data redacted |

## Environment Variables

| Variable                        | Description                    | Default  |
| ------------------------------- | ------------------------------ | -------- |
| `MAIVN_API_KEY`                 | API key for authentication     | Required |
| `MAIVN_TIMEOUT`                 | HTTP request timeout (seconds) | 600      |
| `MAIVN_TOOL_EXECUTION_TIMEOUT`  | Per-tool timeout (seconds)     | 900      |
| `MAIVN_DEPENDENCY_WAIT_TIMEOUT` | Dependency resolution timeout  | 300      |
| `MAIVN_TOTAL_EXECUTION_TIMEOUT` | Total session timeout          | 7200     |
| `MAIVN_ENABLE_BACKGROUND_EXECUTION` | Background tool execution | True     |
| `MAIVN_LOG_LEVEL`               | Logging level                  | INFO     |
| `MAIVN_DEPLOYMENT_TIMEZONE`     | Server timezone                | UTC      |

`MAIVN_ENABLE_BACKGROUND_EXECUTION` controls whether tool calls are dispatched
via a background thread pool. Set it to `false` to force inline, sequential
execution for more deterministic runs.

## Documentation

### API Reference

- [API Reference Index](docs/api/README.md)
- [Agent](docs/api/agent.md) - Agent class reference
- [Swarm](docs/api/swarm.md) - Swarm class reference
- [Client](docs/api/client.md) - Client class reference
- [Decorators](docs/api/decorators.md) - Dependency decorators
- [Configuration](docs/api/configuration.md) - Configuration system
- [MCP](docs/api/mcp.md) - MCP server integration
- [Messages](docs/api/messages.md) - Message types
- [Logging](docs/api/logging.md) - Logging system

### Guides

- [Getting Started](docs/guides/getting-started.md) - First steps
- [Tools](docs/guides/tools.md) - Tool definition patterns
- [Dependencies](docs/guides/dependencies.md) - Dependency management
- [Structured Output](docs/guides/structured-output.md) - Final tool pattern
- [Multi-Agent](docs/guides/multi-agent.md) - Swarm orchestration
- [Private Data](docs/guides/private-data.md) - Security and secrets
- [System Tools](docs/guides/system-tools.md) - Built-in tools
- [Maivn Studio](docs/guides/maivn-studio.md) - Studio UI + API reference

### Reference

- [Troubleshooting](docs/troubleshooting.md) - Common errors and debugging
- [Best Practices](docs/best-practices.md) - Production patterns

## Development

### Setup

```bash
cd libraries/maivn
uv sync
```

### Testing

```bash
uv run pytest
uv run pytest --cov=maivn --cov-report=term-missing
```

Coverage gate: 80% line coverage, excluding terminal reporter UI modules and MCP integration
modules as defined in `pyproject.toml` under `tool.coverage`.

### Linting

```bash
uv run ruff check .
uv run ruff format .
uv run pyright
```

## Releases

- `CI` runs on pull requests and pushes to the default branch (`master` today).
- `Publish PyPI` runs on version tags that match `v*`.
- Configure PyPI Trusted Publishing for this repository before the first release.
- See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the full GitHub and PyPI release procedure.

For standalone repo verification before `maivn-shared` is published, use a local checkout of
`maivn-shared`. The GitHub Actions workflow injects that temporary `uv` source override
automatically.

## License

See [LICENSE](LICENSE) for details.
