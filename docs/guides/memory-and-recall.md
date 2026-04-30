# Memory and Recall

Memory keeps long-running conversations accurate by combining:

- summarization when thread context grows too large
- retrieval of relevant memory signals (vector, keyword, graph, skills, insights, resources)
- asynchronous post-finalize indexing and extraction

## How It Works

Each invocation can include three core stages:

1. Summarize: compacts history before execution when needed.
2. Retrieve: injects relevant memory context into the current turn.
3. Index: persists new memory after the response is returned.

Indexing is asynchronous by design, so latency-sensitive responses are not blocked by persistence work.

## Thread Recall vs Scope-Shared Assets

Memory is intentionally split into two layers:

- Thread recall: summaries, vector hits, keyword hits, graph neighbors, and other transcript-like recall stay tied to the current `thread_id`.
- Scope-shared assets: skills, bound resources, and promoted insights are reusable across future threads within their sharing scope (`agent`, `swarm`, `project`, or `org`).

This means future threads already benefit from scoped skills and resources.
AI-generated insights are narrower by default: auto extraction only supports `agent` or `swarm`, and broader reuse happens through portal promotion to `project` or `org`.

## Memory Levels

`MemoryConfig.level` is the canonical control for retrieval/persistence behavior.

- `none`: no retrieval, no persistence.
- `glimpse`: retrieval only.
- `focus`: retrieval + vector persistence.
- `clarity`: retrieval + vector + graph persistence.

Effective behavior is policy-gated server-side (org policy + subscription tier + runtime safety checks).
If requested settings exceed allowed limits, runtime behavior is safely downscoped.

## Quick Start

Use a stable `thread_id` and set scope defaults:

```python
from maivn import (
    Agent,
    MemoryConfig,
    MemoryInsightExtractionConfig,
    MemoryRetrievalConfig,
    MemorySkillExtractionConfig,
)
from maivn.messages import HumanMessage

agent = Agent(
    name="memory_demo",
    system_prompt="Use prior context when relevant.",
    api_key="your-api-key",
    memory_config=MemoryConfig(
        enabled=True,
        level="clarity",  # none | glimpse | focus | clarity
        summarization_enabled=True,
        persistence_mode="vector_plus_graph",
        retrieval=MemoryRetrievalConfig(
            top_k=4,
            candidate_limit=6,
            skills_enabled=True,
            insights_enabled=True,
            resources_enabled=True,
        ),
        skill_extraction=MemorySkillExtractionConfig(
            enabled=True,
            sharing_scope="project",
        ),
        insight_extraction=MemoryInsightExtractionConfig(
            enabled=True,
            sharing_scope="agent",
        ),
    ),
)

thread_id = "customer-success-q1"

agent.invoke(
    messages=[HumanMessage(content="Store this rollout plan and owners.")],
    thread_id=thread_id,
)

result = agent.invoke(
    messages=[HumanMessage(content="Who owns the rollout milestones?")],
    thread_id=thread_id,
)
```

## Scope Memory Assets

`Agent` and `Swarm` can define memory assets directly:

- `skills`: user-defined reusable skill payloads
- `resources`: bound resource payloads (inline content, file, URL, or document reference)

The SDK packages these assets as typed `MemoryAssetsConfig` on the request rather than
requiring you to pass `memory_defined_skills` or `memory_bound_resources` through
free-form invocation metadata.

```python
agent = Agent(
    name="deploy_agent",
    api_key="your-api-key",
    memory_config=MemoryConfig(
        level="clarity",
        skill_extraction=MemorySkillExtractionConfig(sharing_scope="project"),
    ),
    skills=[
        {
            "skill_id": "deploy-checklist-v1",
            "name": "deploy_with_health_checks",
            "description": "Deploy, run health checks, then cut over traffic.",
            "steps": [
                {"index": 1, "action": "deploy service", "tool": "deploy_service"},
                {"index": 2, "action": "run health checks", "tool": "run_health_checks"},
            ],
        }
    ],
    resources=[
        {
            "name": "deploy-runbook.txt",
            "mime_type": "text/plain",
            "text_content": "Rollback immediately if canary validation fails.",
            "binding_type": "agent",
            "sharing_scope": "agent",
            "tags": ["deploy", "rollback"],
        }
    ],
)
```

## Message Attachments

`HumanMessage` and `RedactedMessage` support attachments.
Attachment payloads are normalized and sent as document candidates for registration.

```python
from maivn.messages import HumanMessage

message = HumanMessage(
    content="Use the attached runbook for this request.",
    attachments=[
        {
            "name": "runbook.txt",
            "mime_type": "text/plain",
            "text_content": "Escalate if error rate exceeds 2%.",
            "sharing_scope": "project",
            "tags": ["runbook", "ops"],
        }
    ],
)
```

Supported attachment inputs include `content_base64`, `content_bytes`, `text_content`, and `file`.

## Per-Invocation Overrides

Override scope defaults per call with `memory_config`:

```python
from maivn import MemoryConfig, MemoryRetrievalConfig

result = agent.invoke(
    messages=[HumanMessage(content="Use retrieval only for this turn.")],
    thread_id=thread_id,
    memory_config=MemoryConfig(
        level="glimpse",
        retrieval=MemoryRetrievalConfig(
            skills_enabled=True,
            insights_enabled=True,
            resources_enabled=True,
            skill_injection_max_count=3,
            insight_injection_max_count=2,
            resource_injection_max_count=2,
        ),
    ),
)
```

## Observe Memory and Resource Events

Use event streaming to inspect enrichment phases:

```python
events = []

def on_event(event: dict) -> None:
    if event.get("event") != "enrichment":
        return
    payload = event.get("payload") or {}
    phase = payload.get("phase")
    if isinstance(phase, str) and (
        phase.startswith("memory_") or phase.startswith("document_")
    ):
        events.append(
            {
                "phase": phase,
                "message": payload.get("message"),
                "memory": payload.get("memory"),
            }
        )

agent.events(include="enrichment", on_event=on_event).invoke(
    messages=[HumanMessage(content="Recall the project constraints.")],
    thread_id=thread_id,
)
```

Common memory phases:

- `memory_summarizing`
- `memory_summarized`
- `memory_retrieving`
- `memory_retrieved`
- `memory_indexing`
- `memory_graph_extracting`
- `memory_indexed`
- `memory_skill_extracting`
- `memory_insight_extracting`

Common resource phases:

- `resource_registering`
- `resource_registered`
- `resource_dedup_reused`
- `resource_version_superseded`
- `resource_extracting`
- `resource_extracted`

`payload.memory` can include hit counts, per-signal counts (`skill_hits`, `insight_hits`, `resource_hits`), latency, and effective memory level.

## Async Post-Finalize Behavior

`memory_indexed`, `memory_skill_extracting`, and `memory_insight_extracting` can appear after the final response event. This is expected.

For strict verification workflows:

- inspect session event history by `session_id`
- run a follow-up recall query and confirm `memory_retrieved` with non-zero hit counts
- allow a short wait between seed and recall in automated tests

## Configuration Reference

Use `MemoryConfig` for public SDK memory controls.

Top-level fields:

- `enabled`
- `level` (`none | glimpse | focus | clarity`)
- `summarization_enabled`
- `persistence_mode` (`persist_none | vector_only | vector_plus_graph`)
- `retrieval`
- `skill_extraction`
- `insight_extraction`

Nested retrieval fields:

- `top_k`
- `candidate_limit`
- `skills_enabled`
- `insights_enabled`
- `resources_enabled`
- `skill_injection_max_count`
- `insight_injection_max_count`
- `resource_injection_max_count`
- `insight_relevance_floor`

Nested extraction fields:

- `skill_extraction.enabled`
- `skill_extraction.sharing_scope`
- `skill_extraction.confidence_threshold`
- `skill_extraction.max_count`
- `insight_extraction.enabled`
- `insight_extraction.sharing_scope`
- `insight_extraction.max_count`
- `insight_extraction.min_relevance_score`

Notes:

- Reserved memory-control keys are not allowed in invocation `metadata`; use `memory_config`
  or scope-level `skills`/`resources`.
- Prefer stable `thread_id` reuse across turns.
- `insight_extraction.sharing_scope` only accepts `agent` or `swarm` for AI-generated insights.
- Promote durable lessons to `project` or `org` from the Developer Portal when broader reuse is warranted.
- Policy/tier constraints still apply server-side even when `memory_config` requests higher capability.

## Related Guides

- [mAIvn Studio](maivn-studio.md)
- [Studio Authoring and Debugging](maivn-studio-authoring-and-debugging.md)
