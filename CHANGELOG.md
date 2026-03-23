# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Developer Portal Foundation**: Multi-tenancy support with organizations and project scoping.
- **Organization Support**: New `iam` schema with `organizations`, `organization_members`, and `user_profiles` tables.
- **Project-Organization Linking**: Projects now belong to organizations via `organization_id` foreign key.
- **Checkpoint Consolidation (v2)**: Unified `execution.checkpoint_data` table replaces four separate metadata tables.
- **Tool Registry Project Scoping**: All tool storage operations now support optional `project_id` parameter for multi-tenant isolation.
- **Session Project Context**: `SessionCore.project_id` and `AuthenticatedUser.project_id` fields for project-aware sessions.
- **New RPC Functions**: `save_checkpoint_bundle_v2` and `get_checkpoint_bundle_v2` for consolidated checkpoint storage.
- **Webhooks System**: Complete webhook infrastructure with HMAC signing, automatic retries, and event subscriptions.
  - Domain entities: `Webhook`, `WebhookDelivery`, `WebhookEvent`
  - Repositories: In-memory and Supabase implementations
  - API routes for webhook CRUD and delivery history
  - Event emitter with convenience methods for common event types

### Changed

- Renamed `user_data` to `private_data` across the SDK/server/agents.
- Renamed `depends_on_data` to `depends_on_private_data`.
- `ToolStorageProtocol` methods now accept optional `project_id` parameter.
- Namespace format updated to support project context: `("tools", "user_id:project_id:hash")`.

### Refactored

- **Concurrency safety**: Added thread-safe locking to `BackgroundExecutor` and `ToolExecutionOrchestrator`.
- **Tool execution**: Introduced strategy pattern for tool execution dispatch (`execution_strategy.py`).
- **Factory hierarchy**: Flattened 4-level nested `legacy_services/factories/` into single-level `core/tool_specs/`.
- **API layer**: Split `mcp.py` into focused modules (`mcp.py`, `mcp_auto.py`, `mcp_tools.py`).
- **Event handling**: Extracted event handlers from `EventStreamProcessor` into `event_handlers.py`.
- **Reporting**: Created shared `reporter_base.py` for common formatting utilities.
- **Orchestrator**: Consolidated helper files and introduced `OrchestratorConfig` for configuration grouping.

### Security

- Agents receive schema-only `private_data_schema` (no values); raw `private_data` values are injected server-side during tool execution.
- Tool results are redacted before being included in any LLM-visible context.
- Added append-only private data audit records (receive/access) in `maivn-server`.
