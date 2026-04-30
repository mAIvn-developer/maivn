# mAIvn Studio

mAIvn Studio is a local UI plus HTTP/SSE API for discovering demos, running multi-turn sessions, and inspecting execution events in real time.

## Start Studio

For an installed public package:

```bash
pip install maivn-studio
maivn studio
```

Run `maivn studio` from the directory that contains your `maivn_studio.json`
file. Studio discovers that config from the current working directory and then
walks up parent directories.

For monorepo development:

```bash
uv sync
cd apps/maivn-demos
uv run maivn studio
```

To launch the companion package directly:

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

Studio config filename is `maivn_studio.json` (underscore).
In this repo, the default shared config is `apps/maivn-demos/maivn_studio.json`.

Key sections:

- `studio`: name, host, port, debug
- `env`: environment file and required variables
- `discovery`: demo scan paths/excludes
- `demos`: explicit demo definitions (and optional variant overrides)
- `saved_prompts`: persisted prompts shown in Studio

```json
{
  "studio": {
    "name": "MAIVN Demo Studio",
    "host": "127.0.0.1",
    "port": 8088,
    "debug": true
  },
  "discovery": {
    "paths": ["demos/core", "demos/features", "demos/projects"],
    "exclude": ["__pycache__", ".pytest_cache", "conftest"]
  },
  "demos": []
}
```

## Demo Discovery and Metadata

Studio combines two sources:

1. Auto-discovered demos from `discovery.paths`
2. Explicit `demos[]` in config (these override discovered entries with the same `id`)

Prompt discovery order:

1. `DEMO_PROMPTS`
2. `DEFAULT_PROMPT`
3. module-level `messages` (`HumanMessage`)
4. source-code fallback for literal `HumanMessage(content=...)`

Invocation defaults:

- `DEMO_INVOCATION` lets demos prefill execution options in UI/API.
- Supported keys: `model`, `reasoning`, `force_final_tool`, `targeted_tools`, `metadata`, `memory_config`, `system_tools_config`, `orchestration_config`, `allow_private_in_system_tools`.
- This is the right place to preload system-tool controls such as `allowed_tools` or `approved_compose_artifact_targets` for Studio users.

Variant behavior:

- Variants can be defined in `maivn_studio.json` or inferred from argparse flags.
- If a module exposes `configure_variant(variant: str | None)`, Studio calls it before execution.

For example, the standalone `compose_artifact` demo can expose:

```python
DEMO_INVOCATION = {
    "force_final_tool": True,
    "system_tools_config": {
        "allowed_tools": ["compose_artifact"],
        "approved_compose_artifact_targets": ["validate_query_artifact.query"],
    },
}
```

This allows Studio runs to enforce the same runtime boundaries as direct SDK invocation.

![Studio demos and variant selection](/maivn_studio/maivn_studio_demos.png "Studio demos with variant selection")

## HTTP API

Base URL: `http://{host}:{port}` from `maivn_studio.json`.

Core utility endpoints:

- `GET /health`
- `GET /config`

Discovery endpoints:

- `POST /api/discovery/scan`
- `POST /api/discovery/apply`

Demo endpoints:

- `GET /api/demos`
- `GET /api/demos/categories`
- `GET /api/demos/{demo_id}`
- `GET /api/demos/{demo_id}/details`

Runtime patch endpoints:

- `PATCH /api/demos/{demo_id}`
- `PATCH /api/demos/{demo_id}/agents/{agent_name}`
- `PATCH /api/demos/{demo_id}/swarms/{swarm_name}`

Saved prompts:

- `GET /api/prompts`
- `POST /api/prompts`
- `DELETE /api/prompts/{prompt_id}`

### Sessions

- `POST /api/sessions` (create/start)
- `GET /api/sessions`
- `GET /api/sessions/{session_id}`
- `POST /api/sessions/{session_id}/messages`
- `POST /api/sessions/{session_id}/interrupt`
- `POST /api/sessions/{session_id}/end`
- `POST /api/sessions/{session_id}/cancel`
- `DELETE /api/sessions/{session_id}` (compat cancel alias)
- `GET /api/sessions/{session_id}/history`
- `GET /api/sessions/{session_id}/events` (SSE)

Create-session request supports:

- `demo_id`, `variant`, `thread_id`
- `message`, `message_type` (`human`, `redacted`, `system`)
- `system_message`
- `private_data`
- `structured_output`
- `invocation`
- `batch`
- `attachments`

Attachment note: Studio API attachments require `content_base64` plus optional metadata fields (`name`, `mime_type`, `sharing_scope`, `binding_type`, `source_url`, `source_type`, `description`, `tags`).

![Studio chat input and run controls](/maivn_studio/maivn_studio_chat_input.png "Session creation starts from the Studio chat input")

### Batch Matrix Sessions

Studio can create one grouped batch turn by passing `batch` to
`POST /api/sessions`. The preferred Studio payload is `batch.rows`; each row is a
Batch Matrix item with its own prompt and optional overrides:

- `label`
- `message`
- `variant`
- `model`
- `reasoning`
- `system_message`
- `targeted_tools`

```json
{
  "demo_id": "batch-invocation",
  "message": "1. API 500\n2. API 429",
  "batch": {
    "enabled": true,
    "rows": [
      {
        "label": "API 500",
        "message": "Summarize incident BATCH-1001",
        "variant": "agent-sync",
        "targeted_tools": ["emit_incident_summary"]
      },
      {
        "label": "API 429",
        "message": "Summarize incident BATCH-1002",
        "variant": "swarm-sync",
        "system_message": "Prioritize incident triage details."
      }
    ],
    "max_concurrency": 2,
    "async_mode": true
  }
}
```

`batch.messages` remains supported for simple uniform batches. Uniform batches
use the same top-level variant and invocation settings for every item and can run
through SDK `batch()` or `abatch()`. Matrix rows with row-level overrides use
row-specific invocations while preserving the same ordering and concurrency
semantics.

## SSE Event Stream

Connect to:

```bash
curl -N http://127.0.0.1:8088/api/sessions/{session_id}/events
```

Each SSE frame contains JSON with:

- `id`
- `type`
- `data`
- `timestamp`

Studio treats the backend SSE stream as an app-facing contract, not as raw SDK transport. Payloads are normalized around the public `maivn.events` schema and include `contract_version`, `event_name`, `event_kind`, and nested descriptors such as `tool`, `assistant`, `assignment`, `enrichment`, `interrupt`, `output`, and `error_info` while preserving legacy flat fields for compatibility.

Studio inherits the shared `maivn.events.EventBridge` normalization contract. Known mAIvn bridge event families are standardized before they enter SSE history or replay, and the bridge canonicalizes long-lived instance IDs for tools, agent assignments, and enrichment scopes so Studio can preserve one logical card/timeline entry per runtime instance.

Studio also keeps a small app-specific dedupe layer on top of that shared contract. Its job is narrow: suppress duplicate logical deliveries that can occur when overlapping Studio delivery paths surface the same interrupt or adjacent identical status message. It does not replace raw-stream normalization or shared bridge identity logic.

Because Studio is a trusted developer tool, it uses the bridge in `internal` mode and preserves full observability payloads for debugging. Do not copy that setting into customer-facing browser apps. For end-user frontends, use `EventBridge(..., audience="frontend_safe")` instead.

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

## Memory Demos in Studio

The default demos config includes two end-to-end memory demos:

- `memory-end-to-end`
- `memory-asset-lifecycle`

Both provide module-level `DEMO_PROMPTS` and `DEMO_INVOCATION.memory_config`, so you can run memory retrieval/persistence flows directly in Studio without re-entering memory settings every turn.

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

For redaction workflows, Studio now keeps dedicated activity cards for preview and session-start redaction phases. Those cards expose:

- inserted placeholder keys
- values added to `private_data`
- merged private data after preview
- matched and unmatched caller-supplied known PII values

That full visibility is intentional in Studio because it is a developer-facing internal tool. Customer-facing frontends should not expose those raw fields; use the safe bridge audience described in [Frontend Events](frontend-events.md).

## Related Guides

- [Memory and Recall](memory-and-recall.md)
- [Studio Authoring and Debugging](maivn-studio-authoring-and-debugging.md)
