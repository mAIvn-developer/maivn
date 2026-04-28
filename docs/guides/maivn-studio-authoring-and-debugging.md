# Studio Authoring and Debugging

This guide focuses on building demos that work cleanly in mAIvn Studio and on debugging them quickly through the Studio API.

## Authoring Studio-Friendly Demos

For reliable Studio behavior, each demo module should expose:

- at least one top-level `Agent` or `Swarm`
- `DEMO_PROMPTS` (recommended)
- optional `DEMO_INVOCATION`
- optional `configure_variant(variant: str | None)`

### Prompt Metadata

`DEMO_PROMPTS` entries support:

- `name`
- `content`
- `description`
- `is_default`
- `structured_output` (tool name)
- `message_type` (`human` or `redacted`)
- `variant` (pre-select demo variant)

```python
DEMO_PROMPTS = [
    {
        "name": "Seed Memory",
        "content": "Store this deployment runbook...",
        "description": "Seed turn for memory lifecycle",
        "is_default": True,
        "message_type": "human",
        "variant": "focus",
    }
]
```

### Invocation Defaults

`DEMO_INVOCATION` lets Studio prefill execution options:

```python
DEMO_INVOCATION = {
    "model": "balanced",
    "reasoning": "medium",
    "memory_config": {
        "enabled": True,
        "level": "focus",
        "summarization_enabled": True,
    },
}
```

Supported keys:

- `model`
- `reasoning`
- `force_final_tool`
- `targeted_tools`
- `metadata`
- `memory_config`
- `allow_private_in_system_tools`

Use `metadata` when the demo depends on system-tool runtime boundaries. For example, a Studio-friendly `compose_artifact` demo can pre-approve its intended target arg:

```python
DEMO_INVOCATION = {
    'force_final_tool': True,
    'metadata': {
        'allowed_system_tools': ['compose_artifact'],
        'approved_compose_artifact_targets': ['validate_query_artifact.query'],
    },
}
```

This keeps Studio runs aligned with the same policy checks enforced in normal SDK invocation.

### Variant Hook

Use `configure_variant` when your module must switch runtime wiring:

```python
def configure_variant(variant: str | None) -> None:
    if variant == "focus":
        ...
    elif variant == "clarity":
        ...
```

## Discovery Workflow

Use Studio discovery endpoints instead of hand-editing every demo entry:

1. `POST /api/discovery/scan` to list candidates from configured paths.
2. `POST /api/discovery/apply` to persist selected demos into `maivn_studio.json`.

This keeps config aligned with actual modules and avoids drift in `id/module/category`.

## Introspection Workflow

Use `GET /api/demos/{demo_id}/details` before debugging runs. It exposes:

- resolved agents and swarms
- tool inventory and dependencies
- discovered prompts
- `privateDataSchema`
- `defaultInvocation` (from `DEMO_INVOCATION`)

This endpoint is the fastest way to confirm that Studio is reading your module as expected.

## Session Debugging Workflow

1. `POST /api/sessions` with a controlled test message.
2. Attach SSE stream: `GET /api/sessions/{session_id}/events`.
3. Submit required interrupts via `POST /api/sessions/{session_id}/interrupt`.
4. Continue with `POST /api/sessions/{session_id}/messages`.
5. Inspect `GET /api/sessions/{session_id}/history` for replayable event history.

Studio is intentionally developer-internal and keeps full bridge visibility for debugging. If you reuse the same event patterns in a customer-facing app, switch your backend bridge to `EventBridge(..., audience="frontend_safe")` instead of copying Studio's internal visibility model.

If you are debugging duplicate activity in Studio, separate transport replay from logical duplicate delivery:

- replay duplicates usually indicate a reconnect/history issue
- repeated interrupts or adjacent identical status messages usually indicate overlapping producer paths

Studio suppresses the latter category in its app adapter while still relying on the shared `maivn.events` normalization and bridge contract underneath.

## Batch Matrix Debugging

Use Studio's Batch Matrix when you need to compare prompts, variants, models, or
targeted tools in one grouped turn. Each matrix row becomes one batch item and
can override `variant`, `model`, `reasoning`, `system_message`, and
`targeted_tools` without changing the demo module.

For a simple uniform batch, send `batch.messages`; Studio can call SDK
`batch()` or `abatch()` directly. For row-level overrides, send `batch.rows`;
Studio executes row-specific invocations under the configured `max_concurrency`
while preserving input order in the grouped result.

The batch SSE sequence is:

- `batch_start`: pending row metadata
- `batch_item_complete`: one completed row payload
- `batch_complete`: aggregate status and all item results

## Runtime Patching Without Restart

Studio supports live runtime edits:

- `PATCH /api/demos/{demo_id}`
- `PATCH /api/demos/{demo_id}/agents/{agent_name}`
- `PATCH /api/demos/{demo_id}/swarms/{swarm_name}`

Use this for rapid prompt/description/tag/limit updates while diagnosing behavior.
Demo patches persist in config; agent/swarm runtime patches apply to the live loaded instance.

## Memory-Specific Debugging Tips

For memory demos:

- reuse the same `thread_id` across turns
- verify enrichment events: `memory_retrieving`, `memory_retrieved`, `memory_indexing`
- expect some memory/document extraction phases after `final`
- validate recall using a follow-up query and check hit counts in enrichment payloads

If retrieval appears empty:

1. confirm `memory_config.level` is at least `glimpse`
2. confirm thread continuity (`thread_id`)
3. allow short delay for async post-finalize indexing

## Common API Errors

- `400`: invalid demo variant
- `404`: unknown demo/session/agent/swarm id
- `422`: invalid message/invocation payload (including bad attachment base64)

## Related Guides

- [mAIvn Studio](maivn-studio.md)
- [Memory and Recall](memory-and-recall.md)
