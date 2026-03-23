# System Tools Guide

The maivn server provides built-in system tools for common capabilities like web search, code execution, artifact synthesis, and reasoning.

## Overview

System tools are server-side tools that extend agent capabilities:

| Tool               | Description                                                          |
| ------------------ | -------------------------------------------------------------------- |
| `web_search`       | Search the web for current information                               |
| `repl`             | Execute Python code in a sandbox                                     |
| `think`            | Route to optimal LLM for complex reasoning                           |
| `compose_artifact` | Synthesize a substantial downstream artifact for a specific tool arg |
| `reevaluate`       | Insert a planning checkpoint before continuing execution             |

These tools are **not** defined in your SDK code - they're injected by the server when appropriate.

## Built-in Capabilities

### Datetime Awareness

The maivn system has built-in datetime awareness. Agents automatically know the current date and time without needing a custom tool. You do **not** need to create a `get_current_time()` tool - the system handles this natively.

```python
# No need for this - the system already knows the time:
# @agent.toolify()
# def get_current_time() -> dict: ...

# Just ask directly
response = agent.invoke([
    HumanMessage(content='What day of the week is it?')
])
```

This applies to:

- Current date and time
- Timezone information
- Date calculations and comparisons

### Configuring Timezone

By default, the SDK auto-detects your system's timezone. You can explicitly configure the timezone using the `Client`:

```python
from maivn import Agent, Client

# Explicit timezone configuration
client = Client(
    api_key='your-api-key',
    client_timezone='America/New_York',  # IANA timezone identifier
)

agent = Agent(name='scheduler', client=client)
```

#### Timezone Parameters

| Parameter              | Type          | Default | Description                                                   |
| ---------------------- | ------------- | ------- | ------------------------------------------------------------- |
| `client_timezone`      | `str \| None` | `None`  | IANA timezone (e.g., `'America/New_York'`, `'Europe/London'`) |
| `auto_detect_timezone` | `bool`        | `True`  | Auto-detect system timezone when `client_timezone` is not set |

#### When to Configure Timezone

- **Server applications**: Set explicit timezone for consistent behavior across deployments
- **User-facing apps**: Set timezone based on user preferences
- **Scheduled tasks**: Ensure time-sensitive operations use the correct timezone

```python
# Disable auto-detection, use UTC
client = Client(
    api_key='your-api-key',
    client_timezone='UTC',
    auto_detect_timezone=False,
)

# User-specific timezone
client = Client(
    api_key='your-api-key',
    client_timezone=user_preferences.timezone,
)
```

The agent will use this timezone context when answering time-related questions or performing date calculations.

## Private Data Protection

System tools are designed with built-in privacy protections that automatically safeguard sensitive information:

### Privacy-First Architecture

All system tools follow a strict privacy model:

- **Zero external exposure**: Private data never leaves the server environment
- **Schema-only awareness**: Tools only know about data structure, not values
- **Automatic redaction**: Sensitive data removed from all outputs
- **Audit logging**: All private data access is tracked

### Web Search Privacy

```python
# Private data is automatically excluded from search queries
agent.private_data = {
    'user_email': 'john@example.com',
    'api_key': 'sk-xxx-secret'
}

# When agent searches, private data stays server-side
response = agent.invoke([
    HumanMessage(content='Search for information about this account')
])

# Search query sent to external API:
# "information about account"  # Private data excluded
```

**Privacy guarantees:**

- Private data never included in search queries
- Search results filtered for PII before returning
- No credentials or sensitive data sent to search providers
- Audit trail records what was searched (not what was excluded)

### REPL Code Execution Privacy

```python
@agent.toolify()
@depends_on_private_data(data_key='database_url', arg_name='db_url')
def query_database(query: str, db_url: str) -> dict:
    # Code executes with injected database URL
    connection = connect(db_url)
    return connection.execute(query)

# Agent sees: {'status': 'success', 'rows': 5}
# Actual database URL never appears in output
```

**Privacy guarantees:**

- Private data injected into sandbox only during execution
- Output automatically scanned and redacted
- No code or data persistence between executions
- Isolated execution environment prevents data leakage

### Think Tool Privacy

The think tool operates with privacy by design:

- Receives only metadata about available private data
- Never sees actual private values
- Reasoning outputs automatically redacted if they reference sensitive data
- Ideal for complex logic involving private data concepts

### Automatic Redaction Flow

1. **Input filtering**: Private data stripped before external calls
2. **Execution**: Real values injected in isolated environment
3. **Output scanning**: Results analyzed for sensitive data
4. **Redaction**: Private data replaced with placeholders
5. **Audit**: Access logged with compliance details

## Web Search

### Purpose

Search the web for current information, news, or facts.

### How It Works

1. The agent determines it needs current information
2. Server performs the web search
3. Search results are returned to the agent
4. Agent incorporates results into its response

### Example Use Case

```python
agent = Agent(
    name='research_agent',
    system_prompt='''You are a research assistant.
    When asked about current events or recent information,
    use web search to find accurate data.''',
    api_key='...',
)

response = agent.invoke([
    HumanMessage(content='What are the latest developments in AI?')
])
```

The agent will automatically use `web_search` when it determines current information is needed.

## REPL (Code Execution)

### Purpose

Execute Python code in a secure sandbox.

### How It Works

1. Agent generates Python code to solve a problem
2. Server executes code in an isolated sandbox
3. Execution results (stdout, errors) are returned
4. Agent interprets results and continues

### Example Use Case

```python
agent = Agent(
    name='data_analyst',
    system_prompt='''You are a data analyst.
    When you need to perform calculations or data analysis,
    write Python code and execute it using the REPL.''',
    api_key='...',
)

response = agent.invoke([
    HumanMessage(content='Calculate the compound interest on $10,000 at 5% for 10 years')
])
```

The agent might generate and execute:

```python
principal = 10000
rate = 0.05
years = 10
result = principal * (1 + rate) ** years
print(f"Final amount: ${result:.2f}")
```

### Safety

- Code runs in an isolated sandbox
- No access to host system
- Time and resource limits applied
- Output is captured and returned

## Compose Artifact

### Purpose

Use `compose_artifact` when the agent needs to synthesize a substantial artifact for a downstream tool argument, such as a SQL query, HTML template, policy document, or other long-form structured payload.

### How It Works

1. A downstream tool declares whether a specific argument may use `compose_artifact`
2. The planner sees that policy in tool metadata and arg schema
3. The server checks the policy again when `compose_artifact` is invoked
4. The downstream tool execution also validates whether the argument actually did or did not come from `compose_artifact`

### Declaring Arg Policy in the SDK

```python
from maivn import Agent, compose_artifact_policy

agent = Agent(name='artifact_agent', api_key='...')

@compose_artifact_policy('query', mode='require', approval='explicit')
@agent.toolify(description='Validate a SQL query artifact')
def validate_query_artifact(query: str) -> dict:
    return {'validated': True, 'query': query}
```

Supported modes:

- `forbid`: the arg must not consume `compose_artifact`
- `allow`: the arg may consume `compose_artifact`
- `require`: the arg must consume `compose_artifact`

Supported approval values:

- `none`: no additional approval metadata required
- `explicit`: invocation metadata must explicitly approve that target arg

### Invocation Metadata

Use invocation metadata to narrow which system tools are allowed for a run and which `compose_artifact` targets are explicitly approved:

```python
response = agent.invoke(
    [HumanMessage(content='Draft and validate the SQL artifact')],
    force_final_tool=True,
    metadata={
        'allowed_system_tools': ['compose_artifact'],
        'approved_compose_artifact_targets': ['validate_query_artifact.query'],
    },
)
```

Approval matching supports:

- `tool_name.arg_name`
- `tool_name.*`
- `*`

### When to Use It

Use `compose_artifact` when:

- the downstream argument expects a large, reusable artifact
- you want clear provenance between synthesis and validation/execution
- you want policy enforcement at both planning time and runtime

Prefer direct tool args when the content is short and does not need a dedicated artifact synthesis step

## Think

### Purpose

Route complex reasoning tasks to the optimal LLM.

### How It Works

1. Agent encounters a complex reasoning task
2. Server routes to the best model for the task type
3. Reasoning is performed with appropriate capabilities
4. Results are returned to the agent

### When It's Used

- Complex mathematical reasoning
- Multi-step logical deduction
- Tasks requiring extended context
- Problems needing specialized capabilities

## System Prompt Guidance

Guide the agent to use system tools appropriately:

### For Research Tasks

```python
agent = Agent(
    name='researcher',
    system_prompt='''You are a research assistant.

    For current information or recent events:
    - Use web search to find up-to-date data
    - Verify information from multiple sources

    For calculations or data analysis:
    - Write and execute Python code
    - Show your work and explain results''',
    api_key='...',
)
```

### For Analysis Tasks

```python
agent = Agent(
    name='analyst',
    system_prompt='''You are a data analyst.

    When analyzing data:
    - Use Python code execution for calculations
    - Visualize results when helpful
    - Explain your methodology

    If you discover your initial approach won't work:
    - Adjust your analysis plan accordingly''',
    api_key='...',
)
```

### Controlling System Tools

You can guide when system tools should (or shouldn't) be used:

```python
agent = Agent(
    name='calculator',
    system_prompt='''You are a simple calculator.
    Only use basic arithmetic - do NOT use web search or code execution.
    Provide quick mental math answers.''',
    api_key='...',
)
```

When you need hard runtime boundaries, pass invocation metadata:

```python
response = agent.invoke(
    [HumanMessage(content='Validate the generated SQL artifact')],
    metadata={
        'allowed_system_tools': ['compose_artifact'],
        'approved_compose_artifact_targets': ['validate_query_artifact.query'],
    },
)
```

## Interaction with Your Tools

System tools work alongside your custom tools:

```python
@agent.toolify(description='Get product catalog')
def get_products() -> dict:
    return {'products': [...]}

@agent.toolify(final_tool=True)
class MarketReport(BaseModel):
    products: list[str]
    market_trends: str  # May come from web_search
    price_analysis: str  # May come from repl calculations
```

The agent can:

1. Call `get_products` (your tool)
2. Use `web_search` for market trends
3. Use `repl` for price calculations
4. Combine all into `MarketReport`

## Event Tracing

See system tool usage with the event builder:

```python
response = agent.events().invoke(
    [HumanMessage(content='Research AI trends and analyze market data')],
)
```

Output might show:

```
[SYSTEM] Using web_search for: AI trends 2024
[SYSTEM] Executing Python code in sandbox
[TOOL] Calling get_products
[FINAL] Generating MarketReport
```

## Privacy Controls and Auditing

### Audit Trail

All system tools maintain comprehensive audit logs:

```python
# Audit events automatically recorded
{
    'timestamp': '2024-01-15T10:30:00Z',
    'tool': 'web_search',
    'action': 'search_query_filtered',
    'private_data_excluded': ['user_email', 'api_key'],
    'query_sent': 'latest AI developments',
    'compliance': true
}
```

### Configurable Privacy Levels

System tools support different privacy modes:

```python
# Default: Maximum privacy
client = Client(
    api_key='...',
    privacy_mode='strict'  # Exclude all private data
)

# Development: Limited exposure
client = Client(
    api_key='...',
    privacy_mode='development',  # Allow non-sensitive data
    allowed_private_fields=['environment', 'debug_mode']
)
```

### Compliance Features

- **PII detection**: Automatic identification of sensitive information
- **Data minimization**: Only necessary data exposed to tools
- **Retention controls**: Automatic cleanup of temporary data
- **Access logging**: Immutable record of all private data access

## Configuration

System tools are still provisioned by the server, but developers can influence use at the SDK layer:

- Server administrators control availability, quotas, and deployment policy
- Invocation `metadata['allowed_system_tools']` can narrow which system tools may run for a session
- `@compose_artifact_policy(...)` controls whether a specific argument can use `compose_artifact`
- `metadata['approved_compose_artifact_targets']` provides explicit approval for args that require it

Contact your administrator for:

- Enabling/disabling specific system tools
- Rate limits and quotas

## Best Practices

### 1. Design for Privacy

```python
# GOOD: System tools automatically handle privacy
agent = Agent(
    name='secure_analyst',
    system_prompt='''Use web search for public information.
    Use REPL for calculations.
    System tools will automatically protect sensitive data.''',
    api_key='...'
)

# Private data configuration
agent.private_data = {
    'customer_id': '12345',
    'api_key': 'sk-xxx-secret'
}
```

### 2. Trust the Redaction System

```python
# System tools automatically redact outputs
@agent.toolify()
@depends_on_private_data(data_key='secret_key', arg_name='key')
def process_data(data: dict, key: str) -> dict:
    result = external_api_call(data, key)
    # Return can contain sensitive data - system will redact
    return {
        'processed': True,
        'auth_used': key,  # This will be redacted
        'result': result
    }
```

### 3. Combine Privacy Capabilities

```python
# System tools work together while maintaining privacy
@agent.toolify(description='Get user profile')
def get_user_profile(user_id: str) -> dict:
    return {'user_id': user_id, 'preferences': [...]}

# Agent can:
# 1. Get profile (your tool)
# 2. Use web_search for public info about preferences
# 3. Use repl for analysis calculations
# 4. All private data stays protected
```

### 4. Monitor Privacy Compliance

```python
# Enable event tracing to verify privacy behavior
response = agent.events().invoke(
    [HumanMessage(content='Analyze this user\'s data')],
)

# Output shows:
# [PRIVACY] Excluded 2 private fields from web search
# [PRIVACY] Redacted 1 sensitive value from REPL output
# [AUDIT] Private data access logged
```

### 5. Use Explicit Artifact Policies for Sensitive Downstream Args

```python
from maivn import compose_artifact_policy

@compose_artifact_policy('query', mode='require', approval='explicit')
@agent.toolify(description='Run an approved SQL query artifact')
def execute_query(query: str) -> dict:
    return {'executed': True}
```

This keeps artifact synthesis intentional, reviewable, and enforceable at runtime.

## See Also

- [Agent API](../api/agent.md) - Agent configuration
- [Decorators API](../api/decorators.md) - Dependency and arg policy decorators
- [Tools Guide](tools.md) - Custom tool patterns
- [Structured Output Guide](structured-output.md) - Combining with final tools
