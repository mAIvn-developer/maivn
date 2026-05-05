# Logging

The maivn SDK provides a logging system for debugging and monitoring agent execution.

## Import

```python
from maivn import configure_logging, get_logger
```

## configure_logging()

Initialize the SDK's process-wide logger. Optional `log_file_path` writes log
records to a file. Console logging is off by default; set `MAIVN_LOG_LEVEL`
before importing/configuring SDK logging when you want console output.

```python
def configure_logging(log_file_path: Path | str | None = None) -> MaivnSDKLogger
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `log_file_path` | `Path \| str \| None` | `None` | Optional path to write log records. When `None`, no file logging is configured |

### Returns

The process-wide `MaivnSDKLogger` instance. Subsequent calls return the same
singleton — passing a different `log_file_path` after first configuration is a
no-op.

### Example

```python
from pathlib import Path
from maivn import configure_logging

log_file = Path(__file__).parent / 'logs' / 'sdk.log'
logger = configure_logging(log_file)

logger.info('SDK initialized')
```

## get_logger()

Return the SDK's process-wide logger. The first call configures the logger
lazily; subsequent calls return the same instance.

```python
def get_logger(log_file_path: Path | str | None = None) -> MaivnSDKLogger
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `log_file_path` | `Path \| str \| None` | `None` | Forwarded to `configure_logging` on first call. Ignored on subsequent calls because the logger is a process-wide singleton |

> The SDK does not expose named/child loggers. There is no `name` parameter and
> no `get_logger('tools')` / `get_logger('agents')` pattern — use Python's
> standard `logging.getLogger(__name__)` if you need module-scoped loggers in
> your own code.

### Example

```python
from maivn import get_logger

logger = get_logger()
logger.debug('Debug message')
logger.info('Info message')
logger.warning('Warning message')
logger.error('Error message')
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MAIVN_LOG_LEVEL` | Console log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `OFF` |
| `MAIVN_LOG_FORMAT` | Custom format string | SDK default |
| `MAIVN_ENABLE_TIMING_LOGS` | Enable timing logs (`true`/`false`) | `true` |

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

### Sample App Pattern

Common pattern for a self-contained sample app:

```python
from pathlib import Path
from maivn import Agent, configure_logging

# Centralized log location
LOG_FILE = Path(__file__).parent.parent.parent / 'logs' / 'sdk_app.log'
LOG_FILE.parent.mkdir(exist_ok=True)

logger = configure_logging(LOG_FILE)
logger.info(f'Starting app: {__file__}')

agent = Agent(
    name='sample_agent',
    api_key='...',
)

# ... app code ...

logger.info('App completed')
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

When console logging is enabled or a file path is configured, records use the
configured formatter:

```
2024-01-15 10:30:45,123 - maivn - INFO - Agent initialized: my_agent
2024-01-15 10:30:45,456 - maivn - DEBUG - Compiling tools for agent
2024-01-15 10:30:46,789 - maivn - INFO - Session started: abc-123
```

### With Timing Logs

When `MAIVN_ENABLE_TIMING_LOGS=true` and logging output is enabled:

```
2024-01-15 10:30:45,123 - maivn - INFO - [TIMING] Tool execution: 1.234s
2024-01-15 10:30:46,789 - maivn - INFO - [TIMING] Total session: 3.456s
```

## Best Practices

1. **Initialize early**: Call `configure_logging()` before creating agents
2. **Use log directories**: Store logs in a dedicated `logs/` directory
3. **Set appropriate levels**: Use `MAIVN_LOG_LEVEL=DEBUG` for console debugging, or keep console logging off and rely on a log file in production
4. **Check logs for errors**: Review logs when debugging issues

## See Also

- [Configuration](configuration.md) - Logging configuration options
- [Troubleshooting](../troubleshooting.md) - Debugging with logs
