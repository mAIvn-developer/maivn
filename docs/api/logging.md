# Logging

The maivn SDK provides a logging system for debugging and monitoring agent execution.

## Import

```python
from maivn import configure_logging, get_logger
```

## configure_logging()

Initialize SDK logging with a file path.

```python
def configure_logging(log_file_path: Path | str) -> Logger
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `log_file_path` | `Path \| str` | Path to the log file |

### Returns

A configured `Logger` instance.

### Example

```python
from pathlib import Path
from maivn import configure_logging

log_file = Path(__file__).parent / 'logs' / 'sdk.log'
logger = configure_logging(log_file)

logger.info('SDK initialized')
```

## get_logger()

Get the SDK logger instance.

```python
def get_logger(name: str | None = None) -> Logger
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str \| None` | Optional logger name (for sub-loggers) |

### Example

```python
from maivn import get_logger

logger = get_logger()
logger.debug('Debug message')
logger.info('Info message')
logger.warning('Warning message')
logger.error('Error message')
```

### Named Loggers

```python
# Create named sub-loggers
tool_logger = get_logger('tools')
agent_logger = get_logger('agents')

tool_logger.info('Tool executed')
agent_logger.info('Agent invoked')
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MAIVN_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |
| `MAIVN_LOG_FORMAT` | Custom format string | SDK default |
| `MAIVN_ENABLE_TIMING_LOGS` | Enable timing logs (`true`/`false`) | `false` |

### Log Levels

| Level | Description |
|-------|-------------|
| `DEBUG` | Detailed diagnostic information |
| `INFO` | General operational messages |
| `WARNING` | Unexpected but handled situations |
| `ERROR` | Errors that need attention |

## Usage Patterns

### Basic Setup

```python
from pathlib import Path
from maivn import Agent, configure_logging

# Configure logging before creating agents
log_path = Path('logs/maivn.log')
log_path.parent.mkdir(exist_ok=True)
configure_logging(log_path)

# Create and use agent
agent = Agent(name='my_agent', api_key='...')
```

### Demo Pattern

Common pattern used in maivn demos:

```python
from pathlib import Path
from maivn import Agent, configure_logging

# Centralized log location
LOG_FILE = Path(__file__).parent.parent.parent / 'logs' / 'sdk_demo.log'
LOG_FILE.parent.mkdir(exist_ok=True)

logger = configure_logging(LOG_FILE)
logger.info(f'Starting demo: {__file__}')

agent = Agent(
    name='demo_agent',
    api_key='...',
)

# ... demo code ...

logger.info('Demo completed')
```

### Event Builder Tracing

For debugging, use `events()` to stream/report execution events:

```python
response = agent.events().invoke(
    [HumanMessage(content='Debug this')],
)
```

This outputs execution progress to the terminal (or your custom event sink), separate from file logging.

## Log Output

### Default Format

```
2024-01-15 10:30:45,123 - maivn - INFO - Agent initialized: my_agent
2024-01-15 10:30:45,456 - maivn - DEBUG - Compiling tools for agent
2024-01-15 10:30:46,789 - maivn - INFO - Session started: abc-123
```

### With Timing Logs

When `MAIVN_ENABLE_TIMING_LOGS=true`:

```
2024-01-15 10:30:45,123 - maivn - INFO - [TIMING] Tool execution: 1.234s
2024-01-15 10:30:46,789 - maivn - INFO - [TIMING] Total session: 3.456s
```

## Best Practices

1. **Initialize early**: Call `configure_logging()` before creating agents
2. **Use log directories**: Store logs in a dedicated `logs/` directory
3. **Set appropriate levels**: Use `DEBUG` for development, `INFO` for production
4. **Check logs for errors**: Review logs when debugging issues

## See Also

- [Configuration](configuration.md) - Logging configuration options
- [Troubleshooting](../troubleshooting.md) - Debugging with logs
