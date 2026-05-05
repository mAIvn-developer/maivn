# Configuration

The maivn SDK uses a layered configuration system that supports environment variables and programmatic configuration.

This page covers SDK environment and process-level settings. Invocation-time runtime
controls such as memory, system tools, orchestration, structured output, and swarm
transport are documented in [Session Config Models](session-config.md).

## Import

```python
from maivn import (
    ConfigurationBuilder,
    MaivnConfiguration,
    get_configuration,
)
```

## MaivnConfiguration

Top-level configuration model containing all SDK settings.

```python
class MaivnConfiguration:
    server: ServerConfiguration
    execution: ExecutionConfiguration
    security: SecurityConfiguration
    logging: LoggingConfiguration
```

### ServerConfiguration

Server connection settings.

| Field                 | Type    | Default | Description          |
| --------------------- | ------- | ------- | -------------------- |
| `timeout_seconds`     | `float` | `600.0` | HTTP request timeout |
| `max_retries`         | `int`   | `3`     | Max retry attempts   |
| `deployment_timezone` | `str`   | `'UTC'` | Server timezone      |

### ExecutionConfiguration

Execution timing and limits.

| Field                             | Type            | Default  | Description                                                              |
| --------------------------------- | --------------- | -------- | ------------------------------------------------------------------------ |
| `default_timeout_seconds`         | `float`         | `600.0`  | Default execution timeout                                                |
| `pending_event_timeout_seconds`   | `float`         | `0.2`    | Event polling timeout                                                    |
| `max_parallel_tools`              | `int`           | `8`      | Max parallel tool executions                                             |
| `enable_background_execution`     | `bool`          | `True`   | Run tool execution using background threads                              |
| `tool_execution_timeout_seconds`  | `float`         | `900.0`  | Per-tool timeout (15 min)                                                |
| `dependency_wait_timeout_seconds` | `float`         | `300.0`  | Dependency resolution timeout                                            |
| `total_execution_timeout_seconds` | `float \| None` | `7200.0` | Total session timeout (2 hours)                                          |
| `max_prompt_length_for_tool_name` | `int`           | `30`     | Max prompt length for naming (overridable via `MAIVN_MAX_PROMPT_LENGTH`) |
| `tool_name_hash_modulo`           | `int`           | `10000`  | Hash modulo for tool IDs (overridable via `MAIVN_TOOL_NAME_HASH_MODULO`) |

### SecurityConfiguration

Authentication settings.

| Field             | Type          | Default | Description                                              |
| ----------------- | ------------- | ------- | -------------------------------------------------------- |
| `api_key`         | `str \| None` | `None`  | API key for authentication                               |
| `require_api_key` | `bool`        | `True`  | Whether the SDK refuses to send unauthenticated requests |

### LoggingConfiguration

Logging settings.

| Field                | Type   | Default                                                  | Description        |
| -------------------- | ------ | -------------------------------------------------------- | ------------------ |
| `level`              | `str`  | `'INFO'`                                                 | Log level          |
| `format_string`      | `str`  | `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'` | Log format string  |
| `enable_timing_logs` | `bool` | `True`                                                   | Enable timing logs |

## ConfigurationBuilder

Factory for creating configuration from various sources.

### from_environment()

Create configuration from environment variables.

```python
config = ConfigurationBuilder.from_environment()
```

This is the recommended way to configure the SDK in production.

## Environment Variables

| Variable                            | Config Path                                 | Description                                  |
| ----------------------------------- | ------------------------------------------- | -------------------------------------------- |
| `MAIVN_API_KEY`                     | `security.api_key`                          | API key for authentication                   |
| `MAIVN_TIMEOUT`                     | `server.timeout_seconds`                    | HTTP request timeout                         |
| `MAIVN_MAX_RETRIES`                 | `server.max_retries`                        | Max retry attempts                           |
| `MAIVN_DEPLOYMENT_TIMEZONE`         | `server.deployment_timezone`                | Server timezone                              |
| `MAIVN_EXECUTION_TIMEOUT`           | `execution.default_timeout_seconds`         | Default timeout                              |
| `MAIVN_PENDING_EVENT_TIMEOUT`       | `execution.pending_event_timeout_seconds`   | Event timeout                                |
| `MAIVN_MAX_PARALLEL_TOOLS`          | `execution.max_parallel_tools`              | Parallel limit                               |
| `MAIVN_ENABLE_BACKGROUND_EXECUTION` | `execution.enable_background_execution`     | Background execution (false = inline)        |
| `MAIVN_TOOL_EXECUTION_TIMEOUT`      | `execution.tool_execution_timeout_seconds`  | Per-tool timeout                             |
| `MAIVN_DEPENDENCY_WAIT_TIMEOUT`     | `execution.dependency_wait_timeout_seconds` | Dependency timeout                           |
| `MAIVN_TOTAL_EXECUTION_TIMEOUT`     | `execution.total_execution_timeout_seconds` | Total timeout                                |
| `MAIVN_MAX_PROMPT_LENGTH`           | `execution.max_prompt_length_for_tool_name` | Max prompt length used when naming tools     |
| `MAIVN_TOOL_NAME_HASH_MODULO`       | `execution.tool_name_hash_modulo`           | Hash modulo applied when generating tool IDs |
| `MAIVN_LOG_LEVEL`                   | `logging.level`                             | Log level                                    |
| `MAIVN_LOG_FORMAT`                  | `logging.format_string`                     | Log format                                   |
| `MAIVN_ENABLE_TIMING_LOGS`          | `logging.enable_timing_logs`                | Timing logs                                  |

## Configuration Functions

### get_configuration()

Get the current active configuration.

```python
from maivn import get_configuration

config = get_configuration()
print(config.server.timeout_seconds)
```

If no configuration is set, returns a default configuration.

For most applications, configuration is read once at process start from environment
variables (see below) and does not need to be mutated at runtime. To override values,
construct a fresh `Client` (or `Agent`) with the explicit fields you want to change —
client-level values take precedence over the active configuration.

## Examples

### Environment-Based Configuration

```bash
# .env file
MAIVN_API_KEY=your-api-key
MAIVN_TIMEOUT=60
MAIVN_TOOL_EXECUTION_TIMEOUT=600
MAIVN_LOG_LEVEL=DEBUG
```

```python
from maivn import ConfigurationBuilder, Agent

# Loads from environment
config = ConfigurationBuilder.from_environment()

# Agent uses this configuration
agent = Agent(name='my_agent', api_key=config.security.api_key)
```

### Checking Configuration

```python
from maivn import get_configuration

config = get_configuration()

print(f'API Key set: {bool(config.security.api_key)}')
print(f'Timeout: {config.server.timeout_seconds}s')
print(f'Tool timeout: {config.execution.tool_execution_timeout_seconds}s')
print(f'Log level: {config.logging.level}')
```

### Client with Custom Timeouts

```python
from maivn import Client

# Override specific timeouts
client = Client(
    api_key='...',
    tool_execution_timeout=600,  # 10 minutes
    dependency_wait_timeout=120,  # 2 minutes
    total_execution_timeout=3600,  # 1 hour
)
```

Client-level timeouts override configuration defaults.

## Configuration Hierarchy

Timeout values are resolved in this order:

1. **Client constructor** - Explicit values passed to `Client()`
2. **Configuration** - Values from `MaivnConfiguration`
3. **Defaults** - Built-in SDK defaults

```python
# Example: tool_execution_timeout resolution
# 1. client.tool_execution_timeout if set
# 2. config.execution.tool_execution_timeout_seconds if set
# 3. Default: 900 seconds
```

## Background Execution

`execution.enable_background_execution` controls whether tool calls are dispatched
through the background executor (thread pool). When set to `False`, tool execution
is performed inline and sequentially, which can improve determinism but may reduce
throughput.

## See Also

- [Client](client.md) - Client timeout configuration
- [Session Config Models](session-config.md) - Typed invocation runtime controls
- [Logging](logging.md) - Logging configuration
