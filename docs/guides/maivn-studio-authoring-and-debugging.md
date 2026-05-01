# Studio Authoring and Debugging

This guide focuses on building apps that work cleanly in mAIvn Studio and on
debugging them quickly.

## Authoring Studio-Friendly Apps

For reliable Studio behavior, each app module should expose:

- at least one top-level `Agent` or `Swarm`
- `APP_PROMPTS` (recommended)
- optional `APP_INVOCATION`
- optional `configure_variant(variant: str | None)`

### Prompt Metadata

`APP_PROMPTS` entries support:

- `name`
- `content`
- `description`
- `is_default`
- `structured_output` (tool name)
- `message_type` (`human` or `redacted`)
- `variant` (pre-select an app variant)

```python
APP_PROMPTS = [
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

`APP_INVOCATION` lets Studio prefill execution options:

```python
APP_INVOCATION = {
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
- `system_tools_config`
- `orchestration_config`
- `allow_private_in_system_tools`

Use `system_tools_config` when the app depends on system-tool runtime boundaries.
For example, a Studio-friendly `compose_artifact` app can pre-approve its intended target arg:

```python
APP_INVOCATION = {
    'force_final_tool': True,
    'system_tools_config': {
        'allowed_tools': ['compose_artifact'],
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

When you add a new app, point Studio's discovery at the directory that holds
it. Studio rescans on demand from the **Scan Repo** action in the catalog and
merges the result with apps you have already approved.

This keeps config aligned with actual modules and avoids drift in
`id/module/category`.

## Introspection Workflow

Open an app in Studio's catalog and click into the **Config** tab to confirm
how Studio resolved your module:

- resolved agents and swarms
- tool inventory and dependencies
- discovered prompts
- private-data schema
- default invocation (from `APP_INVOCATION`)

This is the fastest way to confirm that Studio is reading your module as
expected.

## Session Debugging Workflow

1. Pick the app, optionally choose a variant, send a controlled test message.
2. Watch the **Events** tab for the live SSE stream.
3. Resolve any `interrupt_required` prompts inline.
4. Send follow-ups in the same session to keep `thread_id` continuity.
5. Use the per-event detail view for replayable event history.

Studio runs locally for the developer who owns the data, so it keeps full
bridge visibility for debugging. If you reuse the same event patterns in a
customer-facing app, switch your backend bridge to
`EventBridge(..., audience="frontend_safe")` to apply the safe redaction layer.

If you are debugging duplicate activity in Studio, separate transport replay
from logical duplicate delivery:

- replay duplicates usually indicate a reconnect/history issue
- repeated interrupts or adjacent identical status messages usually indicate
  overlapping producer paths in your own emission code

## Batch Matrix Debugging

Use Studio's Batch Matrix when you need to compare prompts, variants, models,
or targeted tools in one grouped turn. Each matrix row becomes one batch item
and can override `variant`, `model`, `reasoning`, `system_message`, and
`targeted_tools` without changing the app module. Uniform batches reuse the
top-level invocation settings for every item.

The batch SSE sequence is:

- `batch_start`: pending row metadata
- `batch_item_complete`: one completed row payload
- `batch_complete`: aggregate status and all item results

## Runtime Patching Without Restart

Studio supports live edits to a loaded app's prompts, descriptions, tags,
limits, and per-agent / per-swarm settings while a session is running. Use
this for rapid iteration while diagnosing behavior — saved prompt and
description edits persist in `maivn_studio.json`; runtime agent/swarm patches
apply only to the live loaded instance for the rest of the session.

## Memory-Specific Debugging Tips

For memory apps:

- reuse the same `thread_id` across turns
- verify enrichment events: `memory_retrieving`, `memory_retrieved`, `memory_indexing`
- expect some memory/document extraction phases after `final`
- validate recall using a follow-up query and check hit counts in enrichment payloads

If retrieval appears empty:

1. confirm `memory_config.level` is at least `glimpse`
2. confirm thread continuity (`thread_id`)
3. allow short delay for async post-finalize indexing

## Related Guides

- [mAIvn Studio](maivn-studio.md)
- [Memory and Recall](memory-and-recall.md)
