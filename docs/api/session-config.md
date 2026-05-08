# Session Config Models

Typed session config models carry runtime controls for SDK invocations. Use these
objects instead of putting control keys in request `metadata`.

## Import

```python
from maivn import (
    MemoryAssetsConfig,
    MemoryConfig,
    MemoryInsightExtractionConfig,
    MemoryResourceConfig,
    MemoryRetrievalConfig,
    MemorySkillConfig,
    MemorySkillExtractionConfig,
    SessionExecutionConfig,
    SessionOrchestrationConfig,
    StructuredOutputConfig,
    SwarmAgentConfig,
    SwarmConfig,
    SystemToolsConfig,
)
```

`Agent(...)` and `Swarm(...)` accept config objects or equivalent dictionaries
for scope defaults. Invocation methods accept the same config objects as per-call
overrides.

```python
from maivn import Agent, SystemToolsConfig, SessionOrchestrationConfig
from maivn.messages import HumanMessage

agent = Agent(
    name="analyst",
    api_key="...",
    system_tools_config=SystemToolsConfig(allowed_tools=["web_search"]),
)

response = agent.invoke(
    [HumanMessage(content="Research this topic")],
    orchestration_config=SessionOrchestrationConfig(max_cycles=4),
)
```

## Configuration Layers

| Layer               | Where                                            | Merge behavior                                       |
| ------------------- | ------------------------------------------------ | ---------------------------------------------------- |
| Scope default       | `Agent(...)`, `Swarm(...)`                       | Applied to every call from that scope                |
| Invocation override | `invoke()`, `stream()`, `ainvoke()`, `astream()` | Merged over scope defaults for one call              |
| Compiled request    | `compile_state()`                                | Produces a `SessionRequest` with typed config fields |

Request `metadata` is application-owned and intended for your own labels and
correlation IDs. Runtime-control keys (`allowed_system_tools`, `allow_reevaluate_loop`,
`structured_output_intent`, and similar) are rejected at the SDK boundary — use the
typed config objects on this page instead.

## SystemToolsConfig

Controls which system tools are available to a run and which arguments may consume their output.

```python
SystemToolsConfig(
    allowed_tools: list[str] | None = None,
    approved_compose_artifact_targets: list[str] | bool | None = None,
    allow_private_data: bool | None = None,
    allow_private_data_placeholders: bool | None = None,
)
```

| Field                               | Type                        | Default | Description                                                                                                                |
| ----------------------------------- | --------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------- |
| `allowed_tools`                     | `list[str] \| None`         | `None`  | System tool allowlist. Use `[]` to disable all system tools for the call.                                                  |
| `approved_compose_artifact_targets` | `list[str] \| bool \| None` | `None`  | Explicit `compose_artifact` target approvals, or `True` to approve all targets.                                            |
| `allow_private_data`                | `bool \| None`              | `None`  | Allow system tools to receive raw `private_data` values. Advanced use only.                                                |
| `allow_private_data_placeholders`   | `bool \| None`              | `None`  | Allow system tools to receive private-data placeholders. The SDK enables this by default when resolving invocation config. |

```python
agent.invoke(
    messages,
    system_tools_config=SystemToolsConfig(
        allowed_tools=["web_search", "compose_artifact"],
        approved_compose_artifact_targets=["write_report.report"],
    ),
)
```

## SessionOrchestrationConfig

Controls server orchestration loop behavior for both `Agent` and `Swarm`
invocations. Use it to choose whether the runtime should execute a single planned
batch or supervise completed results and allow the orchestrator to create more
actions before producing a final response.

```python
SessionOrchestrationConfig(
    mode: Literal[
        "single_shot_dag",
        "supervisor_loop",
        "strict_user_dag",
        "hybrid",
    ] | None = None,
    final_output_mode: Literal[
        "terminal",
        "supervised",
        "aggregator_only",
    ] | None = None,
    allow_followup_actions: bool | None = None,
    stop_strategy: Literal[
        "orchestrator_decides",
        "final_tool_completed",
        "objective_satisfied",
        "max_cycles",
        "blocker_detected",
    ] | None = None,
    allow_reevaluate_loop: bool | None = None,
    max_cycles: int | None = None,
)
```

| Field                    | Type           | Default | Description                                                                                                                                            |
| ------------------------ | -------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mode`                   | `str \| None`  | `None`  | Planning mode. `None` preserves server defaults. Use `supervisor_loop` for repair or multi-step workflows that should inspect results before stopping. |
| `final_output_mode`      | `str \| None`  | `None`  | Whether a final tool or final-output swarm agent is terminal, supervised, or an aggregator only.                                                       |
| `allow_followup_actions` | `bool \| None` | `None`  | Explicitly allow or disallow more actions after a completed batch. Supervised policies imply this when unset.                                          |
| `stop_strategy`          | `str \| None`  | `None`  | Completion strategy. Use `objective_satisfied` for workflows that must prove a target state before final response.                                     |
| `allow_reevaluate_loop`  | `bool \| None` | `None`  | Allows reevaluate dependencies to continue after a complete result is available.                                                                       |
| `max_cycles`             | `int \| None`  | `None`  | Maximum orchestration loop cycles for this request. Must be greater than zero.                                                                         |

```python
agent.invoke(
    messages,
    orchestration_config=SessionOrchestrationConfig(
        mode="supervisor_loop",
        final_output_mode="supervised",
        allow_followup_actions=True,
        stop_strategy="objective_satisfied",
        allow_reevaluate_loop=True,
        max_cycles=6,
    ),
)
```

Recommended policy choices:

| Workflow                                               | Recommended policy                                                                                |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| Simple Q&A or read-only report                         | `mode="single_shot_dag"`, `final_output_mode="terminal"`                                          |
| Exact user/developer DAG                               | `mode="strict_user_dag"`, `allow_followup_actions=False`                                          |
| Repair-to-green, data cleanup, or validation loops     | `mode="supervisor_loop"`, `final_output_mode="supervised"`, `stop_strategy="objective_satisfied"` |
| Developer-provided first step with autonomous recovery | `mode="hybrid"`, `final_output_mode="supervised"`                                                 |

`max_cycles` defaults to the server's configured orchestration cycle limit when unset.
The server environment variable `MAIVN_AGENTS_MAX_ORCHESTRATION_CYCLES` controls that
default, and invocation-level `max_cycles` can only downscope the server limit.

Policy fields are projected into request metadata for the server runtime:

| Config field | Metadata key |
| ------------ | ------------ |
| `mode` | `orchestration_mode` |
| `final_output_mode` | `final_output_mode` |
| `allow_followup_actions` | `allow_followup_actions` |
| `stop_strategy` | `stop_strategy` |
| `allow_reevaluate_loop` | `allow_reevaluate_loop` |
| `max_cycles` | `max_orchestration_cycles` |

The runtime treats a request as supervised when any of these are true:
`allow_followup_actions=True`, `mode` is `supervisor_loop` or `hybrid`,
`final_output_mode` is `supervised` or `aggregator_only`, or `stop_strategy` is
`orchestrator_decides` or `objective_satisfied`. Supervised sessions can continue
after a final-output agent or final tool produces a result so the orchestrator can
inspect evidence and schedule follow-up actions.

## MemoryConfig

Controls public memory behavior for recall, summarization, and persistence.

```python
MemoryConfig(
    enabled: bool | None = None,
    level: Literal["none", "glimpse", "focus", "clarity"] | None = None,
    summarization_enabled: bool | None = None,
    persistence_mode: Literal[
        "persist_none",
        "vector_only",
        "vector_plus_graph",
    ] | None = None,
    retrieval: MemoryRetrievalConfig | None = None,
    skill_extraction: MemorySkillExtractionConfig | None = None,
    insight_extraction: MemoryInsightExtractionConfig | None = None,
)
```

| Field                   | Type                                                             | Default | Description                                     |
| ----------------------- | ---------------------------------------------------------------- | ------- | ----------------------------------------------- |
| `enabled`               | `bool \| None`                                                   | `None`  | Public memory master toggle for the invocation. |
| `level`                 | `"none" \| "glimpse" \| "focus" \| "clarity" \| None`            | `None`  | Memory behavior level.                          |
| `summarization_enabled` | `bool \| None`                                                   | `None`  | Optional summarization override.                |
| `persistence_mode`      | `"persist_none" \| "vector_only" \| "vector_plus_graph" \| None` | `None`  | Optional persistence downscope.                 |
| `retrieval`             | `MemoryRetrievalConfig \| None`                                  | `None`  | Retrieval limits and signal toggles.            |
| `skill_extraction`      | `MemorySkillExtractionConfig \| None`                            | `None`  | Skill extraction controls.                      |
| `insight_extraction`    | `MemoryInsightExtractionConfig \| None`                          | `None`  | Insight extraction controls.                    |

```python
MemoryRetrievalConfig(
    top_k: int | None = None,
    candidate_limit: int | None = None,
    skills_enabled: bool | None = None,
    insights_enabled: bool | None = None,
    resources_enabled: bool | None = None,
    skill_injection_max_count: int | None = None,
    insight_injection_max_count: int | None = None,
    resource_injection_max_count: int | None = None,
    insight_relevance_floor: float | None = None,
)
```

| Retrieval field                | Type            | Validation                                      |
| ------------------------------ | --------------- | ----------------------------------------------- |
| `top_k`                        | `int \| None`   | `>= 1`                                          |
| `candidate_limit`              | `int \| None`   | `>= 1` and must be `>= top_k` when both are set |
| `skills_enabled`               | `bool \| None`  | Optional skill recall toggle                    |
| `insights_enabled`             | `bool \| None`  | Optional insight recall toggle                  |
| `resources_enabled`            | `bool \| None`  | Optional resource recall toggle                 |
| `skill_injection_max_count`    | `int \| None`   | `>= 1`                                          |
| `insight_injection_max_count`  | `int \| None`   | `>= 1`                                          |
| `resource_injection_max_count` | `int \| None`   | `>= 1`                                          |
| `insight_relevance_floor`      | `float \| None` | `0.0` to `1.0`                                  |

```python
MemorySkillExtractionConfig(
    enabled: bool | None = None,
    sharing_scope: Literal["agent", "swarm", "project", "org"] | None = None,
    confidence_threshold: float | None = None,
    max_count: int | None = None,
)

MemoryInsightExtractionConfig(
    enabled: bool | None = None,
    sharing_scope: Literal["agent", "swarm"] | None = None,
    max_count: int | None = None,
    min_relevance_score: float | None = None,
)
```

Use portal promotion for project-wide or organization-wide insights; AI-generated
insights are limited to `agent` or `swarm` sharing scope in the SDK config.

## MemoryAssetsConfig

Carries user-defined skills and bound resources with one request. Constructor
`skills=[...]` and `resources=[...]` on `Agent` or `Swarm` are normalized into
this config automatically.

```python
MemoryAssetsConfig(
    defined_skills: list[MemorySkillConfig] = [],
    bound_resources: list[MemoryResourceConfig] = [],
    recall_turn_active: bool | None = None,
)
```

| Field                | Type                         | Default | Description                                                 |
| -------------------- | ---------------------------- | ------- | ----------------------------------------------------------- |
| `defined_skills`     | `list[MemorySkillConfig]`    | `[]`    | User-defined skill payloads available for retrieval.        |
| `bound_resources`    | `list[MemoryResourceConfig]` | `[]`    | Bound resource payloads available for retrieval.            |
| `recall_turn_active` | `bool \| None`               | `None`  | Marks a recall-active turn for server-side memory behavior. |

### MemorySkillConfig

```python
MemorySkillConfig(
    skill_id: str | None = None,
    id: str | None = None,
    name: str | None = None,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
    steps: list[dict[str, Any]] = [],
    preconditions: dict[str, Any] = {},
    postconditions: dict[str, Any] = {},
    sharing_scope: Literal["agent", "swarm", "project", "org"] | None = None,
    origin: str | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] = {},
    agent_id: str | None = None,
    swarm_id: str | None = None,
)
```

`MemorySkillConfig` requires either `name` or `title`. `confidence`, when set,
must be between `0.0` and `1.0`.

### MemoryResourceConfig

```python
MemoryResourceConfig(
    resource_id: str | None = None,
    id: str | None = None,
    title: str | None = None,
    name: str | None = None,
    description: str | None = None,
    content: str | None = None,
    source_url: str | None = None,
    url: str | None = None,
    content_base64: str | None = None,
    mime_type: str | None = None,
    tags: list[str] = [],
    binding_type: str | None = None,
    sharing_scope: Literal["agent", "swarm", "project", "org"] | None = None,
    source_type: str | None = None,
    agent_id: str | None = None,
    swarm_id: str | None = None,
)
```

`MemoryResourceConfig` requires at least one of `title`, `name`, `resource_id`,
or `id`.

When `resource_id` is provided with fresh `content_base64`, the server compares the supplied
content hash to the stored resource. Matching content reuses and rebinds the existing resource;
changed content registers a new version and marks the previous active resource `superseded`.

## SwarmConfig

Carries typed swarm orchestration transport data. Most users should configure
`Swarm(...)` and member `Agent(...)` instances instead of constructing this
directly. It is exposed for advanced integrations and inspection.

```python
SwarmConfig(
    invocation_intent: bool | None = None,
    swarm_id: str | None = None,
    swarm_name: str | None = None,
    swarm_description: str | None = None,
    swarm_system_prompt: str | None = None,
    agent_roster: list[SwarmAgentConfig] = [],
    agent_invocation_tool_map: dict[str, str] = {},
    agent_invocation: bool | None = None,
    use_as_final_output: bool | None = None,
    invoked_agent_id: str | None = None,
    invoked_agent_name: str | None = None,
    included_nested_synthesis: Literal["auto", True, False] | None = None,
    sdk_delivery_mode: str | None = None,
    agent_dependency_context: dict[str, Any] | None = None,
    agent_dependency_context_keys: list[str] | None = None,
)
```

| Field                                                                | Type                              | Description                                                       |
| -------------------------------------------------------------------- | --------------------------------- | ----------------------------------------------------------------- |
| `invocation_intent`                                                  | `bool \| None`                    | Marks the request as a swarm invocation.                          |
| `swarm_id`, `swarm_name`, `swarm_description`, `swarm_system_prompt` | `str \| None`                     | Swarm identity and prompt context.                                |
| `agent_roster`                                                       | `list[SwarmAgentConfig]`          | Typed member roster sent to the server.                           |
| `agent_invocation_tool_map`                                          | `dict[str, str]`                  | Maps generated member invocation tool IDs to agent IDs.           |
| `agent_invocation`                                                   | `bool \| None`                    | Marks a nested agent invocation.                                  |
| `use_as_final_output`                                                | `bool \| None`                    | Marks whether the invoked member should produce the final output. |
| `invoked_agent_id`, `invoked_agent_name`                             | `str \| None`                     | Nested member identity.                                           |
| `included_nested_synthesis`                                          | `"auto" \| True \| False \| None` | Nested synthesis mode for member invocations.                     |
| `sdk_delivery_mode`                                                  | `str \| None`                     | SDK transport mode used by server routing.                        |
| `agent_dependency_context`                                           | `dict[str, Any] \| None`          | Dependency payload for nested agent execution.                    |
| `agent_dependency_context_keys`                                      | `list[str] \| None`               | Dependency context key list for nested execution.                 |

### SwarmAgentConfig

```python
SwarmAgentConfig(
    agent_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    use_as_final_output: bool = False,
    included_nested_synthesis: Literal["auto", True, False] | None = None,
    included_nested_synthesis_guidance: str | None = None,
    has_final_tool: bool = False,
    invocation_tool_id: str | None = None,
    invokes_via_dependency: list[str] = [],
    memory_config: MemoryConfig | None = None,
    memory_defined_skills: list[MemorySkillConfig] = [],
    memory_bound_resources: list[MemoryResourceConfig] = [],
)
```

## StructuredOutputConfig

Carries the typed structured-output transport intent inside `SessionRequest`.
Public code normally uses `agent.structured_output(Model).invoke(...)` or
`agent.invoke(..., structured_output=Model)`, which builds this config.

```python
StructuredOutputConfig(
    enabled: bool | None = None,
    model: str | None = None,
)
```

| Field     | Type           | Default | Description                                                                 |
| --------- | -------------- | ------- | --------------------------------------------------------------------------- |
| `enabled` | `bool \| None` | `None`  | Whether structured-output routing is requested.                             |
| `model`   | `str \| None`  | `None`  | Structured-output model name recorded for server routing and observability. |

## SessionExecutionConfig

Carries SDK execution transport details inside `SessionRequest`. This is usually
produced by the SDK, not supplied directly by application code.

```python
SessionExecutionConfig(
    agent_id: str | None = None,
    timeout: int | float | None = None,
    sdk_delivery_mode: str | None = None,
    client_timezone: str | None = None,
    sdk_deployment_timezone: str | None = None,
)
```

| Field                     | Type                   | Default | Description                                             |
| ------------------------- | ---------------------- | ------- | ------------------------------------------------------- |
| `agent_id`                | `str \| None`          | `None`  | SDK agent identifier.                                   |
| `timeout`                 | `int \| float \| None` | `None`  | Execution timeout. Must be non-negative.                |
| `sdk_delivery_mode`       | `str \| None`          | `None`  | SDK delivery mode used by server-side routing.          |
| `client_timezone`         | `str \| None`          | `None`  | Client IANA timezone used for datetime-aware execution. |
| `sdk_deployment_timezone` | `str \| None`          | `None`  | SDK deployment timezone fallback.                       |

## Metadata Boundary

Use `metadata` for your application data:

```python
agent.invoke(
    messages,
    metadata={"request_id": "req_123", "tenant": "acme"},
)
```

Use typed config objects for SDK/runtime controls:

```python
agent.invoke(
    messages,
    system_tools_config={"allowed_tools": ["web_search"]},
    orchestration_config={"max_cycles": 4},
)
```

The SDK rejects reserved control keys in request `metadata` so config changes
remain typed, validated, and visible in the public API.

## See Also

- [Agent](agent.md) - Agent constructor and invocation arguments
- [Swarm](swarm.md) - Swarm constructor and invocation arguments
- [System Tools Guide](../guides/system-tools.md) - System tool allowlists and approvals
- [Memory and Recall Guide](../guides/memory-and-recall.md) - Memory behavior and assets
