# Tools Guide

Tools are the primary way agents interact with the world. This guide covers all aspects of tool definition.

## Execution Security

**All function tools and MCP tools execute locally in your environment.** Your code never leaves your machine and is never transferred to or executed on maivn servers.

The maivn server only:
- Receives tool schemas (names, descriptions, parameters)
- Orchestrates which tools to call and in what order
- Returns tool call decisions to the SDK

The SDK then executes the actual tool code in your local environment. This architecture ensures:
- Your business logic and code remain private
- Sensitive data processed by tools stays local
- You have full control over what your tools can access

### Hardening stdio MCP Environments

By default, stdio MCP servers inherit the parent environment for compatibility. For tighter control over third-party MCP processes, pass credentials explicitly and disable broad inheritance:

```python
from maivn import MCPServer

mcp_server = MCPServer(
    name='external_tools',
    transport='stdio',
    command='python',
    args=['-m', 'my_mcp_server'],
    inherit_env_allowlist=['OPENAI_API_KEY'],
    env={'SERVICE_TOKEN': 'explicit-token'},
)
```

See [MCP Integration](../api/mcp.md) for the full stdio hardening options.

## Scalable Tool Management

The maivn system is designed for high-performance tool management. Agents and swarms can have **thousands of tools** without degradation in response time or accuracy.

The server handles tool selection and orchestration efficiently regardless of catalog size, so you can:
- Register large numbers of domain-specific tools
- Connect multiple MCP servers with extensive tool catalogs
- Build comprehensive swarms with specialized agents

You don't need to manually limit or partition your tools - the system manages this automatically.

## Tool Types

The maivn SDK supports two types of tools:

1. **Function Tools** - Python functions that execute logic
2. **Model Tools** - Pydantic models for structured output

## Registration Styles

Tools can be registered in three ways. All three styles use the same underlying agent tool
registry and support the same dependency decorators.

### Decorator Registration

Use `@agent.toolify(...)` when defining the tool next to the agent:

```python
agent = Agent(name='helper', api_key='...')

@agent.toolify(description='Add two numbers together')
def add_numbers(a: int, b: int) -> dict:
    return {'sum': a + b}
```

### Constructor Registration

Use `Agent(..., tools=[...])` when functions or models are already defined and the agent
configuration should show the full tool surface:

```python
def add_numbers(a: int, b: int) -> dict:
    """Add two numbers together."""
    return {'sum': a + b}

agent = Agent(name='helper', api_key='...', tools=[add_numbers])
```

### Imperative Registration

Use `agent.add_tool(...)` when tools are imported, assembled conditionally, or need options:

```python
agent = Agent(name='helper', api_key='...')
agent.add_tool(
    add_numbers,
    name='add_numbers',
    description='Add two numbers together',
    tags=['math'],
)
```

For Pydantic final tools, prefer `add_tool(..., final_tool=True)` when using imperative
registration:

```python
class MathAnswer(BaseModel):
    """Return the final math answer."""

    answer: int

agent.add_tool(MathAnswer, name='math_answer', final_tool=True)
```

## Function Tools

### Basic Function Tool

```python
from maivn import Agent

agent = Agent(name='helper', api_key='...')

@agent.toolify(description='Add two numbers together')
def add_numbers(a: int, b: int) -> dict:
    return {'sum': a + b}
```

### Key Requirements

1. **Return a JSON-compatible value**: Tools may return any value that the SDK can
   serialize — `dict`, `list`, primitive (`str`/`int`/`float`/`bool`/`None`), Pydantic
   `BaseModel` (serialized via `model_dump(mode='json')`), dataclass (via
   `dataclasses.asdict`), or `set`/`tuple` (converted to lists). Returning a `dict`
   with named fields is recommended because the LLM consumes the result as JSON and
   benefits from explicit field names, but it is not enforced.
2. **Type hints**: Use type hints for parameters (helps the LLM understand usage)
3. **Description**: Provide a description via `description=` argument or docstring

### Using Pydantic Field for Parameters

Use `Field()` for detailed parameter documentation:

```python
from pydantic import Field
from typing import Annotated

@agent.toolify(description='Search for products')
def search_products(
    query: Annotated[str, Field(description='Search query string')],
    limit: Annotated[int, Field(default=10, description='Max results to return')],
) -> dict:
    return {'results': [...]}
```

### Docstring Descriptions

If you don't provide a `description` argument, the docstring is used:

```python
@agent.toolify()
def get_weather(city: str) -> dict:
    """Get current weather for a city.

    Args:
        city: Name of the city to get weather for.

    Returns:
        Weather data including temperature and conditions.
    """
    return {'city': city, 'temp': 72}
```

## Model Tools

Model tools use Pydantic models for structured output.

### Basic Model Tool

```python
from pydantic import BaseModel, Field

@agent.toolify(description='Generate a weather report')
class WeatherReport(BaseModel):
    """Structured weather report."""
    city: str = Field(..., description='City name')
    temperature: int = Field(..., description='Temperature in Fahrenheit')
    conditions: str = Field(..., description='Weather conditions')
    humidity: int = Field(..., description='Humidity percentage')
```

### Docstring Descriptions for Models

Like function tools, model tools can use the class docstring instead of the `description` argument:

```python
@agent.toolify()  # No description needed - uses docstring
class WeatherReport(BaseModel):
    """Generate a structured weather report for a given location.

    Use this when the user asks for weather information and you want
    to return data in a consistent format.
    """
    city: str = Field(..., description='City name')
    temperature: int = Field(..., description='Temperature in Fahrenheit')
    conditions: str = Field(..., description='Weather conditions')
```

The docstring becomes the tool description that the LLM sees.

### Nested Models

Model tools fully support nested Pydantic models. The LLM receives the complete schema and generates valid nested structures:

```python
class Location(BaseModel):
    """Geographic location."""
    city: str = Field(..., description='City name')
    country: str = Field(..., description='Country name')

class Temperature(BaseModel):
    """Temperature in multiple units."""
    fahrenheit: int = Field(..., description='Temperature in Fahrenheit')
    celsius: int = Field(..., description='Temperature in Celsius')

@agent.toolify(final_tool=True)
class DetailedWeatherReport(BaseModel):
    """Comprehensive weather report with nested data."""
    location: Location
    temperature: Temperature
    conditions: str = Field(..., description='Current weather conditions')
    forecast: list[str] = Field(..., description='Multi-day forecast summaries')
```

**Key points about nested models:**

- Nested models don't need `@agent.toolify()` - only the top-level model is registered as a tool
- Field descriptions in nested models are included in the schema the LLM sees
- You can nest models to any depth
- Lists of models are supported (e.g., `list[Location]`)
- Optional nested models work as expected (e.g., `location: Location | None = None`)

**Decorators and nested models:**

Dependency decorators (`@depends_on_tool`, `@depends_on_private_data`, etc.) can be used on nested models as well as top-level model tools. This allows you to inject dependencies at any level of your model hierarchy:

```python
from maivn import depends_on_tool

class Location(BaseModel):
    """Geographic location with enriched data."""
    city: str
    country: str

@depends_on_tool(fetch_coordinates, arg_name='coords')
class EnrichedLocation(Location):
    """Location with coordinates fetched from external service."""
    latitude: float
    longitude: float

@agent.toolify(final_tool=True)
class WeatherReport(BaseModel):
    """Weather report using enriched location."""
    location: EnrichedLocation  # Nested model with its own dependency
    temperature: int
    conditions: str
```

Dependencies are resolved at each level where they are declared, giving you fine-grained control over data injection throughout your model hierarchy.

**Example with lists of nested models:**

```python
class DailyForecast(BaseModel):
    """Single day forecast."""
    date: str = Field(..., description='Date in YYYY-MM-DD format')
    high: int = Field(..., description='High temperature')
    low: int = Field(..., description='Low temperature')
    conditions: str

@agent.toolify(final_tool=True)
class WeeklyForecast(BaseModel):
    """Week-long weather forecast."""
    location: Location
    days: list[DailyForecast] = Field(..., description='Daily forecasts for the week')
```

## Tool Options

The `@agent.toolify()` decorator accepts several options:

```python
@agent.toolify(
    name='custom_name',           # Override tool name
    description='Tool description', # Description for LLM
    always_execute=False,          # Always run this tool
    final_tool=False,              # Mark as final output tool
    tags=['category', 'type'],     # Tags for organization
    before_execute=callback,       # Hook before execution
    after_execute=callback,        # Hook after execution
)
```

### name

Override the default name (function/class name):

```python
@agent.toolify(name='weather_lookup')
def get_weather(city: str) -> dict:
    ...
```

### description

Provide a clear description for the LLM:

```python
@agent.toolify(description='Fetch current weather data for any city worldwide')
def get_weather(city: str) -> dict:
    ...
```

### always_execute

Force the tool to always run:

```python
@agent.toolify(always_execute=True)
def log_request(request: dict) -> dict:
    # Always executed, regardless of LLM decision
    return {'logged': True}
```

**Note**: `always_execute` and `final_tool` are orthogonal — they describe execution
frequency and output role, respectively, and may be combined on the same tool when
needed.

### final_tool

Mark as the structured output tool (only one per agent):

```python
@agent.toolify(final_tool=True)
class FinalReport(BaseModel):
    summary: str
    data: dict
```

See [Structured Output Guide](structured-output.md) for details.

### tags

Organize tools with tags:

```python
@agent.toolify(tags=['data', 'fetch'])
def fetch_data() -> dict:
    ...

@agent.toolify(tags=['data', 'process'])
def process_data() -> dict:
    ...
```

### Execution Hooks

Add callbacks before/after tool execution:

```python
def log_start(ctx):
    print(f"Starting: {ctx['tool_id']}")

def log_end(ctx):
    print(f"Finished: {ctx['tool_id']}, result: {ctx['result']}")

@agent.toolify(
    before_execute=log_start,
    after_execute=log_end,
)
def my_tool() -> dict:
    return {'done': True}
```

## Return Values

The SDK runs every tool result through `to_jsonable`, which handles dicts, lists,
primitives, Pydantic models, dataclasses, sets, and tuples. Pick whichever shape
fits the operation; named fields (dict or model) tend to read more clearly to the
LLM than positional structures.

### Returning Dictionaries

```python
@agent.toolify()
def get_data() -> dict:
    return {
        'status': 'success',
        'data': [...],
        'count': 10,
    }
```

### Returning Pydantic Models or Dataclasses

```python
from pydantic import BaseModel

class GetDataResult(BaseModel):
    status: str
    data: list[dict]
    count: int

@agent.toolify()
def get_data() -> GetDataResult:
    return GetDataResult(status='success', data=[...], count=10)
```

### Returning Primitives or Lists

```python
@agent.toolify()
def count_items() -> int:
    return 42

@agent.toolify()
def list_active_users() -> list[str]:
    return ['alice', 'bob']
```

### Returning Errors

A common convention is to return a dict with an `'error'` key so the LLM can
reason about failures. This is a recipe, not a hard requirement — raising an
exception or returning any other shape both work.

```python
@agent.toolify()
def risky_operation(path: str) -> dict:
    try:
        result = do_something(path)
        return {'result': result}
    except FileNotFoundError:
        return {'error': f'File not found: {path}'}
```

## Async Tools

Async functions are supported:

```python
@agent.toolify(description='Fetch data asynchronously')
async def fetch_async(url: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return {'data': response.json()}
```

## Tool Listing

List all registered tools:

```python
tools = agent.list_tools()
for tool in tools:
    print(f'{tool.name}: {tool.description}')
```

## Best Practices

### 1. Clear Descriptions

Write descriptions that help the LLM understand when to use the tool:

```python
# Good
@agent.toolify(description='Search product catalog by name, category, or SKU')
def search_products(query: str) -> dict: ...

# Less helpful
@agent.toolify(description='Search')
def search_products(query: str) -> dict: ...
```

### 2. Typed Parameters

Use specific types, not `Any`:

```python
# Good
def process(items: list[str], count: int) -> dict: ...

# Avoid
def process(items, count) -> dict: ...
```

### 3. Descriptive Parameter Names

```python
# Good
def send_email(recipient_email: str, subject: str, body: str) -> dict: ...

# Less clear
def send_email(to: str, s: str, b: str) -> dict: ...
```

### 4. Reasonable Defaults

```python
@agent.toolify()
def search(
    query: str,
    limit: int = 10,
    include_archived: bool = False,
) -> dict:
    ...
```

### 5. Keep Tools Focused

One tool should do one thing well:

```python
# Good: separate tools
@agent.toolify()
def fetch_user(user_id: str) -> dict: ...

@agent.toolify()
def update_user(user_id: str, data: dict) -> dict: ...

# Avoid: one tool doing too much
@agent.toolify()
def manage_user(action: str, user_id: str, data: dict = None) -> dict: ...
```

## See Also

- [Dependencies Guide](dependencies.md) - Chain tools together
- [Structured Output Guide](structured-output.md) - Model tools and final_tool
- [Agent API](../api/agent.md) - `toolify()`, `add_tool(...)`, and `tools=[...]` reference
