# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-06-15

### Fixed

- Restored Python 3.10 compatibility in the event bridge by avoiding
  `datetime.UTC`.
- Corrected SDK docs that drifted from the public API, including
  `SessionResponse` usage, logging defaults, message constructor signatures,
  client memory methods, and authentication examples.

### Added

- **Scheduled Invocation**: New `cron(...)`, `every(...)`, and `at(...)` builders on `BaseScope` (inherited by `Agent` and `Swarm`). Each returns a `CronInvocationBuilder` whose terminal methods (`invoke`, `stream`, `batch`, `abatch`, `ainvoke`, `astream`) start a `ScheduledJob`. Includes `JitterSpec` (uniform/normal/triangular distributions, asymmetric ranges, snap-to-grid, deterministic seed), `Retry` (constant/linear/exponential backoff with `max_delay` cap and exception-class filter), misfire policy (`skip`/`fire_now`/`coalesce`), overlap policy (`skip`/`queue`/`replace` with `max_overlap`), bounded `start_at`/`end_at`/`max_runs`, lifecycle (`start`/`stop`/`pause`/`resume`/`trigger_now`), inspection (`next_run_at`, `next_runs`, `history`, `last_run`, fire/success/failure/skip counters), and callbacks (`on_fire`, `on_success`, `on_error`, `on_skip`). Process-wide `list_jobs()` / `stop_all_jobs()` registry.
- **Async Invocation Surface**: `Agent.ainvoke()` / `Agent.astream()` and `Swarm.ainvoke()` / `Swarm.astream()` mirror the synchronous `invoke` / `stream` for native asyncio code.
- **Runtime Dependency**: `croniter>=2.0.1` (MIT) for cron expression parsing; `python-dateutil` and `six` are pulled in transitively.
- **Project Scoping**: Sessions and tool registration now support an optional project context for multi-tenant isolation.

### Changed

- Renamed `user_data` to `private_data` across the SDK.
- Renamed `depends_on_data` to `depends_on_private_data`.

### Refactored

- **Concurrency safety**: Added thread-safe locking to the background execution and tool-orchestration layers.
- **Tool execution**: Introduced a strategy pattern for tool-execution dispatch.
- **Factory hierarchy**: Flattened a deeply nested factory hierarchy into a single-level module layout.
- **API layer**: Split the MCP integration into focused modules.
- **Event handling**: Refactored event-stream handling into dedicated handler modules.
- **Reporting**: Introduced a shared base for reporter formatting utilities.
- **Orchestrator**: Consolidated orchestrator helper modules and introduced grouped configuration objects.

### Security

- Agents receive schema-only `private_data_schema` (no values); raw `private_data` values are injected at execution time within the runtime.
- Tool results are redacted before being included in any LLM-visible context.
- Added append-only private data audit records (receive/access) within the mAIvn service.
