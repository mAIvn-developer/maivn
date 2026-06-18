# SDK Reference

The complete public surface you import from `maivn` — every class, decorator, config model, and helper, grouped by area with links to the detailed per-class pages.

This page is the hub. If you are building your first agent, start with the [Getting Started guide](../guides/getting-started.md) and the [Agent reference](agent.md); come back here when you need to find an exact name or see how the pieces fit together. This page documents the *behavioral* surface (how to build, invoke, and stream); for the *shapes* that behavior produces and consumes — `SessionResponse`, `TokenUsage`, privacy and billing models — see [Shared Data Models](data-models.md).

## What this is

`maivn` is a Python SDK for building agentic systems. You describe what your agent can do — its tools, its dependencies, its configuration — as plain Python objects, and the SDK runs each request through an orchestrator that decides what to call and when. Everything you need lives behind a single import:

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='assistant', api_key='your-api-key')
response = agent.invoke([HumanMessage(content='Hello!')])
print(response.response)
```

The whole programming surface is the SDK. You construct `Agent` and `Swarm` objects, register tools with decorators, tune behavior with typed config models, and read results off a `SessionResponse`. Tool code runs locally in your environment — the SDK sends only tool schemas (names, descriptions, parameters) for planning.

> **Note**
> `invoke()` returns a `SessionResponse`. Use `response.response` for the final assistant text and `response.result` for structured or final-tool output.

## The public import surface

Everything below is exported from the top-level `maivn` package. The spine reads top-down: the **core classes** you instantiate, the **decorators** that wire tools together, the **configuration** that shapes behavior, then **events**, **scheduling**, and **messages**.

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
    ToolOverride,
    MCPServer,
    MCPAutoSetup,
    MCPSoftErrorHandling,

    # Decorators
    tool_output,
    toolify,
    toolset,
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
    FinalOutputMode,
    OrchestrationMode,
    SessionOrchestrationConfig,
    StopStrategy,
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

    # Billing / Usage
    BillingUsage,
    BillingPlan,
    BillingCurrentUsage,
    BillingUsageBreakdown,
    BillingUsageProject,
    BillingUsageApiKey,
    BillingUsageTotals,

    # Permissions
    PERMISSION_FLAG_NAMES,
    PermissionFlag,
    PermissionSet,
    require_permissions,

    # Provider Metadata
    AuthMode,
    ProviderCapability,
    ProviderMetadata,

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

Message types live in a dedicated submodule:

```python
from maivn.messages import HumanMessage, AIMessage, SystemMessage, RedactedMessage
```

> **Tip**
> You rarely import all of this at once. Most applications need only `Agent` (or `Swarm`), one or two decorators, and the message types. Everything else is opt-in.

## Core classes

The objects you instantiate and call. `Agent` and `Swarm` are the two entry points; both inherit a large shared surface from `BaseScope` (tool registration, structured output, events, batching, scheduling, MCP).

| Class | Description | Reference |
| --- | --- | --- |
| [`Agent`](agent.md) | Primary entry point. Holds tools, config, and credentials, and routes invocations through the orchestrator. Requires either an `api_key` or a `Client`. Exposes `invoke` / `stream` / `ainvoke` / `astream`. | Core |
| [`Swarm`](swarm.md) | Multi-agent orchestration container. Coordinates a list of `Agent`s with shared tool access; the first agent is the entry agent. Adds `add_agent` / `get_agent` / `list_agents` and the `.member` dependency-aware registration builder. | Core |
| [`BaseScope`](agent.md#basescope) | Abstract base shared by `Agent` and `Swarm`. Owns tool registration (`toolify` / `add_tool` / `add_toolset`), `structured_output()`, `events()`, `batch` / `abatch`, scheduling (`cron` / `every` / `at`), and MCP registration. Rarely instantiated directly. | Core |
| [`Client`](client.md) | Connection manager that carries credentials and connects an `Agent` or `Swarm` to the mAIvn service. Usually created for you from an `api_key`. | Core |
| [`ClientBuilder`](client.md#clientbuilder) | Factory for constructing `Client` instances with explicit settings. | Core |
| [Events](events.md) | Public event models, payload builders, and stream-normalization helpers for surfacing execution state to your backend and frontend. | Core |
| [`ToolOverride`](mcp.md) | Per-tool registration override (name, description, tags, metadata, output schema) applied via `add_tool` / `add_toolset` or MCP `tool_overrides`. | Core |
| [`MCPServer`](mcp.md) | Configuration for connecting an external MCP server (stdio or HTTP transport) as a tool provider. MCP tools run locally in your environment. | MCP |
| [`MCPAutoSetup`](mcp.md#mcpautosetup) | Auto-setup helper for `uvx`-based MCP servers. | MCP |
| [`MCPSoftErrorHandling`](mcp.md#mcpsofterrorhandling) | Tolerant retry policy for transient MCP soft errors. | MCP |

### How an agent runs

When you call `invoke()` or `stream()`, the SDK compiles your tools, dependencies, and config into a request and hands it to the orchestrator. The orchestrator plans which tools to call (planning sees only the tool *schemas*, never your code), executes them locally in your process, and synthesizes a response. You read the result off the returned `SessionResponse`, or consume incremental `SSEEvent`s from the streaming variants.

The async methods (`ainvoke` / `astream`) mirror the synchronous ones for native asyncio code, and `batch` / `abatch` run independent requests concurrently while preserving input order.

## Decorators

Decorators define tools and declare how a tool relates to other tools, agents, secret data, and the user. `@toolify` turns a function or Pydantic model into a tool; `@toolset` marks a class whose methods become tools. The `depends_on_*` and `compose_artifact_policy` decorators are applied beneath `@agent.toolify(...)` (or beneath `@swarm.member`), or chained off the builder returned by `toolify(...)`. See the [Dependencies guide](../guides/dependencies.md) for the full data-flow model.

| Decorator | Description | Reference |
| --- | --- | --- |
| [`@toolify`](decorators.md) | Register a function or Pydantic model as a tool. | Decorators |
| [`@toolset`](decorators.md#toolset-decorators) | Mark a class whose `@toolify`-marked methods register as a group of tools. | Decorators |
| [`@depends_on_tool`](decorators.md#depends_on_tool) | Inject another tool's output as an argument. | Decorators |
| [`@depends_on_agent`](decorators.md#depends_on_agent) | Inject another agent's output as an argument (swarm workflows). | Decorators |
| [`@depends_on_private_data`](decorators.md#depends_on_private_data) | Inject a declared private value at execution time; the model plans against the schema and never sees the raw value. | Decorators |
| [`@depends_on_interrupt`](decorators.md#depends_on_interrupt) | Pause to collect user input mid-execution. | Decorators |
| [`@depends_on_await_for`](decorators.md#depends_on_await_for) | Gate a tool behind another tool's run without consuming its output. | Decorators |
| [`@depends_on_reevaluate`](decorators.md#depends_on_reevaluate) | Trigger orchestrator reevaluation after the tool runs, so the plan can adapt. | Decorators |
| [`@compose_artifact_policy`](decorators.md#compose_artifact_policy) | Declare how a tool composes artifacts produced earlier in the run. | Decorators |

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

## Configuration

Two kinds of configuration exist. **SDK configuration** describes the environment — credentials, timeouts, defaults — and is usually built once from the environment. **Session config models** are typed objects that carry per-scope or per-call runtime controls; pass them to `Agent(...)` / `Swarm(...)` as defaults, or to `invoke()` / `stream()` as overrides.

### SDK configuration

| Item | Description | Reference |
| --- | --- | --- |
| [`MaivnConfiguration`](configuration.md#maivnconfiguration) | Top-level SDK environment/configuration model. | Configuration |
| [`ConfigurationBuilder`](configuration.md#configurationbuilder) | Build SDK configuration from environment variables. | Configuration |
| [`get_configuration()`](configuration.md#get_configuration) | Return the current SDK configuration. | Configuration |

### Session config models

| Item | Description | Reference |
| --- | --- | --- |
| [`MemoryConfig`](session-config.md#memoryconfig) | Memory retrieval, summarization, and persistence controls. | Session Config |
| [`SystemToolsConfig`](session-config.md#systemtoolsconfig) | Allowlists and approvals for built-in system tools (`web_search`, `repl`, `think`). | Session Config |
| [`SessionOrchestrationConfig`](session-config.md#sessionorchestrationconfig) | Reevaluate-loop and orchestration cycle controls. | Session Config |
| [`MemoryAssetsConfig`](session-config.md#memoryassetsconfig) | Per-request user-defined skills and resources. | Session Config |
| [`SwarmConfig`](session-config.md#swarmconfig) | Typed swarm transport config. | Session Config |
| [`StructuredOutputConfig`](session-config.md#structuredoutputconfig) | Structured-output transport intent. | Session Config |
| [`SessionExecutionConfig`](session-config.md#sessionexecutionconfig) | SDK execution transport details. | Session Config |
| `FinalOutputMode`, `OrchestrationMode`, `StopStrategy` | Literal aliases used by `SessionOrchestrationConfig`. | Session Config |

Memory has a dedicated family of resource and policy models (`MemoryInsight`, `MemoryResource`, `MemorySkill`, `OrganizationMemoryPolicy`, and related enums). See the [Memory and Recall guide](../guides/memory-and-recall.md) for how they fit together.

### Privacy and private data

These models declare and govern sensitive values so they never reach the model in cleartext. The model plans against a schema of key names and types; real values are injected only at tool-execution time, and tool results are scanned and redacted before returning to the model. See the [Private Data guide](../guides/private-data.md).

| Item | Description | Reference |
| --- | --- | --- |
| `PrivateData` | Declares a known private value with a custom key name, label, type, and format hint. | [Private Data guide](../guides/private-data.md) |
| `RedactedMessage` | A user message whose sensitive spans are detected and substituted with placeholders. | [Private Data guide](../guides/private-data.md) |
| `PIIWhitelist`, `PIIWhitelistEntry` | Mark specific values, patterns, or entity types as safe to pass through, with required justification. `phi_mode` refuses entity-type entries for HIPAA Safe Harbor categories. | [Private Data guide](../guides/private-data.md) |
| `HIPAA_SAFE_HARBOR_CATEGORIES` | Exported constant: the HIPAA Safe Harbor identifier categories. | [Private Data guide](../guides/private-data.md) |
| `RedactionPreviewRequest`, `RedactionPreviewResponse` | Request/response models for previewing redaction via `preview_redaction()`. | [Private Data guide](../guides/private-data.md) |

## Messages

Import message types from `maivn.messages`. These are the inputs you pass to `invoke()` / `stream()` and the assistant turns the SDK returns.

| Class | Description | Reference |
| --- | --- | --- |
| [`HumanMessage`](messages.md#humanmessage) | User input message. | Messages |
| [`AIMessage`](messages.md#aimessage) | Assistant response message. | Messages |
| [`SystemMessage`](messages.md#systemmessage) | System prompt message. | Messages |
| `RedactedMessage` | User message with sensitive data redacted before it reaches the model. | [Private Data guide](../guides/private-data.md) |

## Logging

| Function | Description | Reference |
| --- | --- | --- |
| [`configure_logging()`](logging.md#configure_logging) | Initialize SDK logging (optionally to a file). | Logging |
| [`get_logger()`](logging.md#get_logger) | Return the SDK logger instance. | Logging |

## Events

The events surface lets you stream execution state — tool calls, phase indicators, the final response — into your own backend and frontend. The canonical `normalize_stream()` helper converts a raw stream into a stable, versioned `AppEvent` contract; payload builders construct individual event payloads; and `EventBridge` mounts an SSE endpoint. See the [Events reference](events.md) and the [Frontend Events guide](../guides/frontend-events.md) for client examples across many languages.

| Item | Description | Reference |
| --- | --- | --- |
| `AppEvent`, `UIEvent`, `RawSSEEvent` | Public event models for normalized, UI, and raw stream events. | [Events](events.md) |
| `APP_EVENT_CONTRACT_VERSION` | Version constant for the `AppEvent` contract. | [Events](events.md) |
| `normalize_stream`, `normalize_stream_event` | Convert raw stream events into the canonical `AppEvent` shape. | [Events](events.md) |
| `build_*_payload` | Builders for individual event payloads (enrichment, final, tool, status message, interrupt, session start, and more). | [Events](events.md) |
| `EventBridge`, `BridgeRegistry`, `BridgeAudience` | Mount and manage an SSE bridge with an audience scope. | [Events](events.md) |
| `EventBridgeSecurityPolicy`, `BackpressurePolicy` | Trust-boundary and backpressure controls for the bridge. | [Events](events.md) |
| `forward_normalized_event`, `forward_normalized_stream` | Forward normalized events/streams to a downstream consumer. | [Events](events.md) |
| `NormalizedStreamState`, `NormalizedEventForwardingState` | State objects for normalization and forwarding. | [Events](events.md) |

> **Note**
> Enrichment events are fixed-label phase indicators (for example, "Evaluating request", "Planning actions", "Synthesizing response") streamed between tool calls so consumers can show what the system is doing. Status messages are a separate, opt-in channel enabled with `stream(status_messages=True)`.

## Scheduling

Any `Agent` or `Swarm` can run on a schedule. `cron(...)`, `every(...)`, and `at(...)` return a chainable builder; calling a terminal method (`invoke` / `stream` / `batch` and async variants) schedules the job and returns a `ScheduledJob` handle. See the [Scheduling reference](scheduling.md) and the [Scheduled Invocation guide](../guides/scheduled-invocation.md).

| Item | Description | Reference |
| --- | --- | --- |
| [`scope.cron(...)` / `every(...)` / `at(...)`](scheduling.md#cron) | Build a recurring, fixed-interval, or one-shot scheduled invocation. | Scheduling |
| [`CronInvocationBuilder`](scheduling.md#croninvocationbuilder) | Chainable builder over `invoke` / `stream` / `batch` and async variants. | Scheduling |
| [`ScheduledJob`](scheduling.md#scheduledjob) | Lifecycle handle returned by terminal builder calls (`on_fire`, `on_error`, `stop`). | Scheduling |
| [`JitterSpec`](scheduling.md#jitterspec), `JitterDistribution` | Bounded randomness around fire times. | Scheduling |
| [`Retry`](scheduling.md#retry), `RetryBackoff` | Retry policy with constant / linear / exponential backoff. | Scheduling |
| `Schedule`, `CronSchedule`, `IntervalSchedule`, `AtSchedule` | Schedule descriptor models. | Scheduling |
| `MisfirePolicy`, `OverlapPolicy` | Policies for missed and overlapping fires. | Scheduling |
| [`RunRecord`](scheduling.md#runrecord), `RunStatus` | Outcome of a single fire. | Scheduling |
| [`list_jobs()` / `stop_all_jobs()`](scheduling.md#module-level-helpers) | Process-wide registry helpers. | Scheduling |

## Token usage and billing

Token usage is captured per LLM call, normalized across providers, and aggregated for the session, so responses surface input/output/total token counts (including cache reads and reasoning tokens) alongside the model and provider. For account-level reporting, the public billing/usage models — `BillingUsage`, `BillingPlan`, `BillingCurrentUsage`, and the `BillingUsageBreakdown` / `BillingUsageProject` / `BillingUsageApiKey` / `BillingUsageTotals` family — expose aggregated usage and plan/quota information via `Client.get_usage()`. See the [Billing reference](billing.md) and the [Billing and Usage guide](../guides/billing-and-usage.md).

## Quick navigation by goal

- **Build your first agent** — [Agent](agent.md) + [Getting Started guide](../guides/getting-started.md)
- **Define and wire tools** — [Decorators](decorators.md) + [Tools guide](../guides/tools.md) + [Dependencies guide](../guides/dependencies.md)
- **Get guaranteed typed output** — [`structured_output()` on Agent](agent.md) + [Structured Output guide](../guides/structured-output.md)
- **Coordinate multiple agents** — [Swarm](swarm.md) + [Multi-Agent guide](../guides/multi-agent.md)
- **Connect external tools** — [MCP](mcp.md)
- **Stream events to a frontend** — [Frontend Events guide](../guides/frontend-events.md) (one-line FastAPI mount + client examples in many languages); [Events](events.md) for the API reference and trust-boundary controls
- **Run agents on a schedule** — [Scheduling](scheduling.md) + [Scheduled Invocation guide](../guides/scheduled-invocation.md)
- **Handle secrets and PII** — [Private Data guide](../guides/private-data.md)
- **Tune memory and recall** — [Session Config Models](session-config.md) + [Memory and Recall guide](../guides/memory-and-recall.md)
- **Configure the SDK environment** — [Configuration](configuration.md)
- **Track tokens and usage** — [Billing](billing.md) + [Billing and Usage guide](../guides/billing-and-usage.md)
- **Debug** — [Logging](logging.md) + [Troubleshooting](../troubleshooting.md)

## Next steps

- [Getting Started](../guides/getting-started.md) — your first agent, end to end
- [Agent reference](agent.md) — the full `Agent` and `BaseScope` surface
- [Shared Data Models](data-models.md) — the typed shapes you read off responses and pass in
- [Examples](../examples/README.md) — a runnable tour organized by what you want to build
- [Best Practices](../best-practices.md) — production patterns

## Version

```python
from maivn import __version__
print(__version__)
```
