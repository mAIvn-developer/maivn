# API Reference

Complete reference documentation for the maivn SDK public API.

## Overview

The maivn SDK exports its public API from the top-level `maivn` package:

```python
from maivn import (
    # Version
    __version__,

    # Core Classes
    Agent,
    Swarm,
    Client,
    ClientBuilder,
    BaseScope,
    MCPServer,
    MCPAutoSetup,
    MCPSoftErrorHandling,

    # Decorators
    depends_on_tool,
    depends_on_agent,
    depends_on_private_data,
    depends_on_interrupt,
    depends_on_await_for,
    depends_on_reevaluate,
    compose_artifact_policy,

    # Configuration
    ConfigurationBuilder,
    MaivnConfiguration,
    get_configuration,
    MemoryAssetsConfig,
    MemoryConfig,
    MemoryInsightExtractionConfig,
    MemoryLevel,
    MemoryPersistenceMode,
    MemoryResourceConfig,
    MemoryRetrievalConfig,
    MemorySharingScope,
    MemorySkillConfig,
    MemorySkillExtractionConfig,
    SessionExecutionConfig,
    SessionOrchestrationConfig,
    StructuredOutputConfig,
    SwarmAgentConfig,
    SwarmConfig,
    SystemToolsConfig,

    # Memory Resources
    MemoryInsight,
    MemoryInsightOrigin,
    MemoryInsightType,
    MemoryPersistenceCeiling,
    MemoryResource,
    MemoryResourceBindingType,
    MemoryResourceDetail,
    MemoryResourceStatus,
    MemorySkill,
    MemorySkillOrigin,
    MemorySkillStatus,
    MemoryUnboundResourceCandidate,
    OrganizationMemoryPolicy,
    OrganizationMemoryPurgeResult,
    ProjectMemoryResources,

    # PII / Privacy
    HIPAA_SAFE_HARBOR_CATEGORIES,
    PIIWhitelist,
    PIIWhitelistEntry,
    PrivateData,
    RedactedMessage,
    RedactionPreviewRequest,
    RedactionPreviewResponse,

    # Logging
    configure_logging,
    get_logger,

    # Scheduling
    AtSchedule,
    CronInvocationBuilder,
    CronSchedule,
    IntervalSchedule,
    JitterDistribution,
    JitterSpec,
    MisfirePolicy,
    OverlapPolicy,
    Retry,
    RetryBackoff,
    RunRecord,
    RunStatus,
    Schedule,
    ScheduledJob,
    list_jobs,
    stop_all_jobs,

    # Events
    APP_EVENT_CONTRACT_VERSION,
    AppEvent,
    BackpressurePolicy,
    BridgeAudience,
    BridgeRegistry,
    EventBridge,
    EventBridgeSecurityPolicy,
    NormalizedEventForwardingState,
    NormalizedStreamState,
    RawSSEEvent,
    UIEvent,
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
    forward_normalized_event,
    forward_normalized_stream,
    normalize_stream,
    normalize_stream_event,

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
| [MCPSoftErrorHandling](mcp.md#mcpsofterrorhandling) | Tolerant retry policy for transient MCP soft errors  | MCP       |

### Decorators

| Decorator                                                         | Description                                  | Reference  |
| ----------------------------------------------------------------- | -------------------------------------------- | ---------- |
| [@depends_on_tool](decorators.md#depends_on_tool)                 | Declare dependency on another tool's output  | Decorators |
| [@depends_on_agent](decorators.md#depends_on_agent)               | Declare dependency on another agent's output | Decorators |
| [@depends_on_private_data](decorators.md#depends_on_private_data) | Inject server-side secret data               | Decorators |
| [@depends_on_interrupt](decorators.md#depends_on_interrupt)       | Collect user input during execution          | Decorators |
| [@depends_on_await_for](decorators.md#depends_on_await_for)       | Gate execution behind another tool's run     | Decorators |
| [@depends_on_reevaluate](decorators.md#depends_on_reevaluate)     | Trigger orchestrator reevaluation after a run | Decorators |
| [@compose_artifact_policy](decorators.md#compose_artifact_policy) | Policy for tools that compose artifacts      | Decorators |

### Configuration

| Item                                                          | Description                                      | Reference     |
| ------------------------------------------------------------- | ------------------------------------------------ | ------------- |
| [MaivnConfiguration](configuration.md#maivnconfiguration)     | Top-level SDK environment/configuration model    | Configuration |
| [ConfigurationBuilder](configuration.md#configurationbuilder) | Build SDK configuration from environment         | Configuration |
| [get_configuration()](configuration.md#get_configuration)     | Get current SDK configuration                    | Configuration |
| [MemoryConfig](session-config.md#memoryconfig)                | Memory retrieval, summarization, and persistence | Session Config |
| [SystemToolsConfig](session-config.md#systemtoolsconfig)      | System-tool allowlists and approvals             | Session Config |
| [SessionOrchestrationConfig](session-config.md#sessionorchestrationconfig) | Reevaluate-loop and orchestration cycle controls | Session Config |
| [MemoryAssetsConfig](session-config.md#memoryassetsconfig)    | Per-request user-defined skills and resources    | Session Config |
| [SwarmConfig](session-config.md#swarmconfig)                  | Typed swarm transport config                     | Session Config |
| [StructuredOutputConfig](session-config.md#structuredoutputconfig) | Structured-output transport intent           | Session Config |
| [SessionExecutionConfig](session-config.md#sessionexecutionconfig) | SDK execution transport details              | Session Config |

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

### Scheduling

| Item | Description | Reference |
| --- | --- | --- |
| [`scope.cron(...)`](scheduling.md#cron) / [`every(...)`](scheduling.md#every) / [`at(...)`](scheduling.md#at) | Build a scheduled invocation | Scheduling |
| [`CronInvocationBuilder`](scheduling.md#croninvocationbuilder) | Chainable builder over `invoke` / `stream` / `batch` and async variants | Scheduling |
| [`JitterSpec`](scheduling.md#jitterspec) | Bounded randomness around fire times | Scheduling |
| [`Retry`](scheduling.md#retry) | Retry policy with constant / linear / exponential backoff | Scheduling |
| [`ScheduledJob`](scheduling.md#scheduledjob) | Lifecycle handle returned by terminal builder calls | Scheduling |
| [`RunRecord`](scheduling.md#runrecord) | Outcome of a single fire | Scheduling |
| [`list_jobs()` / `stop_all_jobs()`](scheduling.md#module-level-helpers) | Process-wide registry helpers | Scheduling |

## Quick Navigation

- **Getting started?** See [Agent](agent.md) and [Decorators](decorators.md)
- **Multi-agent systems?** See [Swarm](swarm.md)
- **Streaming events to your frontend?** Start with the [Frontend Events guide](../guides/frontend-events.md) — one-line FastAPI mount + client examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python, Rust, .NET, and more. For the API reference and trust-boundary controls, see [Events](events.md)
- **External tools?** See [MCP](mcp.md)
- **Configuration?** See [Configuration](configuration.md) for SDK environment settings and [Session Config Models](session-config.md) for invocation runtime controls.
- **Scheduling cron jobs?** See [Scheduling](scheduling.md) and the [Scheduled Invocation guide](../guides/scheduled-invocation.md)
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
    MemoryConfig,
    SystemToolsConfig,
    SessionOrchestrationConfig,
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
