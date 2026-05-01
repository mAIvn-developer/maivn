# mAIvn Studio

mAIvn Studio is a local UI plus HTTP/SSE API for discovering apps, running multi-turn sessions, and inspecting execution events in real time.

## Start Studio

Install the Studio companion as an SDK extra and launch it from the directory
that contains your `maivn_studio.json` config file. Studio discovers the config
from the current working directory and walks up parent directories until it
finds one.

```bash
pip install "maivn[studio]"
maivn studio
```

`maivn[studio]` pulls in the `maivn-studio` package automatically; you can also
invoke its entry point directly if you prefer:

```bash
maivn-studio
```

Optional CLI overrides:

- `--config` / `-c`: explicit `maivn_studio.json` path
- `--host`
- `--port` / `-p`
- `--debug` / `-d`
- `--reload` / `-r`
- `--no-browser`

![Studio overview](/maivn_studio/maivn_studio_overview.png "Studio overview screen")

## Configuration

Studio reads `maivn_studio.json` (note the underscore) from the current working
directory or any parent directory. A minimal config tells Studio where to find
your app modules and how to launch the local UI:

Key sections:

- `studio`: name, host, port, debug
- `env`: environment file and required variables
- `discovery`: app scan paths and excludes
- `saved_prompts`: persisted prompts shown in Studio

```json
{
  "studio": {
    "name": "My Studio",
    "host": "127.0.0.1",
    "port": 8088,
    "debug": true
  },
  "discovery": {
    "paths": ["apps/core", "apps/features", "apps/projects"],
    "exclude": ["__pycache__", ".pytest_cache", "conftest"]
  }
}
```

## App Discovery and Metadata

Studio walks every `discovery.paths` directory and registers each Python
module that defines at least one top-level `Agent` or `Swarm`. You can shape
how an app appears in Studio with these optional module-level constants:

- `APP_PROMPTS`: list of preset prompts shown in the chat composer.
- `APP_INVOCATION`: dict of default execution options applied on every run.
- `DEFAULT_PROMPT`: a single string fallback prompt.
- `messages`: a pre-built list of `HumanMessage` / `RedactedMessage` instances.
- `configure_variant(variant: str | None)`: hook called before execution when
  the user picks a non-default variant.

If an app does not expose `APP_PROMPTS` or `messages`, Studio falls back to
extracting literal `HumanMessage(content=...)` calls from the module source.

`APP_INVOCATION` accepts: `model`, `reasoning`, `force_final_tool`,
`targeted_tools`, `metadata`, `memory_config`, `system_tools_config`,
`orchestration_config`, `allow_private_in_system_tools`. Use it to preload
system-tool controls such as `allowed_tools` or
`approved_compose_artifact_targets` for Studio users.

For example, a standalone `compose_artifact` app can expose:

```python
APP_INVOCATION = {
    "force_final_tool": True,
    "system_tools_config": {
        "allowed_tools": ["compose_artifact"],
        "approved_compose_artifact_targets": ["validate_query_artifact.query"],
    },
}
```

This keeps Studio runs aligned with the same policy checks enforced in normal
SDK invocation.

![Studio app list and variant selection](/maivn_studio/maivn_studio_demos.png "Studio app list with variant selection")

## Sessions and the Chat Composer

Each app launches inside a session: pick an app from the catalog, optionally
choose a variant, then send a message. Studio's composer supports human and
redacted message types, system-message overrides, attachments, structured
output, and Batch Matrix runs.

![Studio chat input and run controls](/maivn_studio/maivn_studio_chat_input.png "Session creation starts from the Studio chat input")

### Batch Matrix

Use the Batch Matrix when you need to compare prompts, variants, models, or
targeted tools in one grouped turn. Each matrix row becomes its own batch item
and can override `variant`, `model`, `reasoning`, `system_message`, and
`targeted_tools` without changing the app module. Uniform batches reuse the
top-level invocation settings for every item.

The batch SSE sequence is:

- `batch_start` — pending row metadata
- `batch_item_complete` — one completed row payload
- `batch_complete` — aggregate status and all item results

## Live Event Stream

Studio renders the public `maivn.events` schema, so each frame carries
`contract_version`, `event_name`, `event_kind`, and the nested descriptors
(`tool`, `assistant`, `assignment`, `enrichment`, `interrupt`, `output`,
`error_info`) you see documented in the [Events API reference](../api/events.md).

Because Studio is a trusted developer tool running on your machine, it uses the
bridge in `internal` mode and preserves full observability payloads for
debugging. Do not copy that setting into customer-facing browser apps — for
end-user frontends, use `EventBridge(..., audience="frontend_safe")` instead.

Common event types:

- `session_start`
- `tool_event`
- `assistant_chunk`
- `system_tool_start`
- `system_tool_chunk`
- `system_tool_complete`
- `agent_assignment`
- `enrichment`
- `interrupt_required`
- `turn_complete`
- `final`
- `batch_start`
- `batch_item_complete`
- `batch_complete`
- `error`
- `session_end`
- `heartbeat`

For the canonical packet contract and bridge-family normalization details, see:

- [Events](../api/events.md)
- [Frontend Events](frontend-events.md)

![Studio event stream view](/maivn_studio/maivn_studio_events.png "Live event stream in Studio")

## Memory Apps in Studio

The default app config includes two end-to-end memory apps:

- `memory-end-to-end`
- `memory-asset-lifecycle`

Both provide module-level `APP_PROMPTS` and an `APP_INVOCATION.memory_config`, so you can run memory retrieval/persistence flows directly in Studio without re-entering memory settings every turn.

For memory lifecycle verification, monitor enrichment phases such as:

- `memory_summarizing`
- `memory_retrieving`
- `memory_retrieved`
- `memory_indexing`
- `memory_skill_extracting`
- `memory_insight_extracting`
- `resource_registering`
- `resource_registered`
- `resource_dedup_reused`
- `redaction_previewed`
- `message_redaction_applied`

`memory_indexed` and extraction phases can appear after the final response because indexing/extraction are asynchronous post-finalize operations.

For redaction workflows, Studio surfaces dedicated activity cards for preview and session-start redaction phases. Those cards expose:

- inserted placeholder keys
- values added to `private_data`
- merged private data after preview
- matched and unmatched caller-supplied known PII values

This visibility is appropriate in Studio because it runs locally for the developer who owns the data. Customer-facing frontends should not expose those raw fields; use the safe bridge audience described in [Frontend Events](frontend-events.md).

## Related Guides

- [Memory and Recall](memory-and-recall.md)
- [Studio Authoring and Debugging](maivn-studio-authoring-and-debugging.md)
