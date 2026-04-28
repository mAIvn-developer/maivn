# API Reference

Complete reference documentation for the maivn SDK public API.

## Overview

The maivn SDK exports its public API from the top-level `maivn` package:

```python
from maivn import (
    # Core Classes
    Agent,
    Swarm,
    Client,
    ClientBuilder,
    BaseScope,
    MCPServer,
    MCPAutoSetup,

    # Decorators
    depends_on_tool,
    depends_on_agent,
    depends_on_private_data,
    depends_on_interrupt,

    # Configuration
    ConfigurationBuilder,
    MaivnConfiguration,
    get_configuration,

    # Logging
    configure_logging,
    get_logger,

    # Events
    AppEvent,
    RawSSEEvent,
    EventBridge,
    BridgeAudience,
    EventBridgeSecurityPolicy,
    normalize_stream,

    # Interrupts
    default_terminal_interrupt,
    get_interrupt_service,
    set_interrupt_service,
)
```

Messages are imported from `maivn.messages`:

```python
from maivn.messages import HumanMessage, AIMessage, SystemMessage
```

## Class Reference

### Core Classes

| Class                                    | Description                                                     | Reference |
| ---------------------------------------- | --------------------------------------------------------------- | --------- |
| [Agent](agent.md)                        | Main agent class for tool registration and invocation           | Core      |
| [Swarm](swarm.md)                        | Multi-agent orchestration container with `member` registration  | Core      |
| [Client](client.md)                      | HTTP connection manager for server communication                | Core      |
| [ClientBuilder](client.md#clientbuilder) | Factory for creating Client instances                           | Core      |
| [BaseScope](agent.md#basescope)          | Base class shared by Agent and Swarm                            | Core      |
| [Events](events.md)                      | Public event models, builders, and stream normalization helpers | Core      |
| [MCPServer](mcp.md)                      | MCP server configuration and client                             | MCP       |
| [MCPAutoSetup](mcp.md#mcpautosetup)      | Auto-setup for uvx-based MCP servers                            | MCP       |

### Decorators

| Decorator                                                         | Description                                  | Reference  |
| ----------------------------------------------------------------- | -------------------------------------------- | ---------- |
| [@depends_on_tool](decorators.md#depends_on_tool)                 | Declare dependency on another tool's output  | Decorators |
| [@depends_on_agent](decorators.md#depends_on_agent)               | Declare dependency on another agent's output | Decorators |
| [@depends_on_private_data](decorators.md#depends_on_private_data) | Inject server-side secret data               | Decorators |
| [@depends_on_interrupt](decorators.md#depends_on_interrupt)       | Collect user input during execution          | Decorators |

### Configuration

| Item                                                          | Description                          | Reference     |
| ------------------------------------------------------------- | ------------------------------------ | ------------- |
| [MaivnConfiguration](configuration.md#maivnconfiguration)     | Top-level configuration model        | Configuration |
| [ConfigurationBuilder](configuration.md#configurationbuilder) | Build configuration from environment | Configuration |
| [get_configuration()](configuration.md#get_configuration)     | Get current configuration            | Configuration |

### Messages

| Class                                      | Description                | Reference |
| ------------------------------------------ | -------------------------- | --------- |
| [HumanMessage](messages.md#humanmessage)   | User input message         | Messages  |
| [AIMessage](messages.md#aimessage)         | Assistant response message | Messages  |
| [SystemMessage](messages.md#systemmessage) | System prompt message      | Messages  |

### Logging

| Function                                            | Description             | Reference |
| --------------------------------------------------- | ----------------------- | --------- |
| [configure_logging()](logging.md#configure_logging) | Initialize SDK logging  | Logging   |
| [get_logger()](logging.md#get_logger)               | Get SDK logger instance | Logging   |

## Quick Navigation

- **Getting started?** See [Agent](agent.md) and [Decorators](decorators.md)
- **Multi-agent systems?** See [Swarm](swarm.md)
- **Streaming events to your frontend?** Start with the [Frontend Events guide](../guides/frontend-events.md) — one-line FastAPI mount + client examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python, Rust, .NET, and more. For the API reference and trust-boundary controls, see [Events](events.md)
- **External tools?** See [MCP](mcp.md)
- **Configuration?** See [Configuration](configuration.md)
- **Debugging?** See [Logging](logging.md)

## Import Patterns

### Minimal Import

```python
from maivn import Agent
from maivn.messages import HumanMessage
```

### Full Import

```python
from maivn import (
    Agent,
    Swarm,
    depends_on_tool,
    depends_on_agent,
    depends_on_private_data,
    depends_on_interrupt,
    configure_logging,
)
from maivn.messages import HumanMessage, SystemMessage
```

### Configuration Import

```python
from maivn import (
    ConfigurationBuilder,
    MaivnConfiguration,
    get_configuration,
)
```

## Version

Access the SDK version:

```python
from maivn import __version__
print(__version__)
```
