# Portal Memory Management

This guide covers memory operations in the mAIvn Developer Portal.

## Scope

Portal memory management is split across:

- Organization-level policy and purge controls
- Project-level memory assets (skills, insights, resources)

Use organization settings for hard governance.
Use project memory pages for operational curation.

Portal curation sits on top of the runtime memory split:

- thread recall stays with the originating `thread_id`
- skills, resources, and promoted insights are reusable assets scoped to `agent`, `swarm`, `project`, or `org`

## Organization Memory Policy

Open the **Organization Settings** page and scroll to the **Memory Management** section.

Controls:

- memory enabled/disabled
- persistence ceiling:
  - `persist_none`
  - `vector_only`
  - `vector_plus_graph`
- optional retention windows:
  - vector retention days
  - graph retention days

These controls set server-side ceilings.
SDK `memory_config` can only request equal or lower behavior than the org ceiling.

## Organization Memory Purge

On the same **Organization Settings** page, scroll to the **Memory Purge** section.

You can purge persisted memory by:

- organization scope (default)
- project scope (`project_id`)
- session scope (`session_id`)

Purge requires explicit confirmation token: `PURGE_MEMORY`.

## Project Memory Skills

Path: `Project -> Memory -> Skills`

Skills are reusable execution patterns with:

- scope (`agent`, `swarm`, `project`, `org`)
- structured steps
- preconditions/postconditions
- status lifecycle (`active`, `deprecated`, `quarantined`)

Use skills to stabilize repeated workflows and improve retrieval quality for known procedures.

## Project Memory Insights

Path: `Project -> Memory -> Insights`

Insights capture operational lessons and warnings with:

- insight types (`lesson`, `warning`, `optimization`, `failure_pattern`)
- relevance score
- TTL / decay controls
- staged scope promotion (`agent`/`swarm` -> `project` -> `org`)

Operational model:

- auto-generated insights start narrow (`agent` or `swarm`)
- promote to `project` when the lesson should benefit future threads across the project
- promote to `org` only when the lesson is broadly reusable across teams

Tier note:

- Insight management is available on Professional and Enterprise tiers.

## Project Memory Resources

Path: `Project -> Memory -> Resources`

Resource capabilities:

- create from upload or source URL
- update metadata/tags/scope
- replace content (version chain)
- bind to `agent` or `swarm`
- soft-delete and restore
- identify cleanup candidates (unbound/idle resources)

Binding types visible in the portal include `portal`, `agent`, `swarm`, `message`, and `unbound`.

Versioning is content-hash based. Re-uploading or binding identical bytes reuses the existing
non-deleted resource. Replacing content, or deploying an SDK-bound resource with the same
`resource_id` but different `content_base64`, creates a new registered version and marks the
previous active row `superseded`.

## Recommended Operating Model

1. Set organization memory ceiling first.
2. Keep project defaults conservative (`glimpse`/`focus`) until needed.
3. Curate high-value skills and resources by scope.
4. Use insight promotion to move durable lessons from agent/swarm to project, then to org only when justified.
5. Schedule periodic cleanup of unbound or superseded resources.

## Related Guides

- [Memory and Recall](memory-and-recall.md)
- [Workspace Operations](workspace-operations.md)
- [API Keys and Webhooks](api-keys-webhooks.md)
