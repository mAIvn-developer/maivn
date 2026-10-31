# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.5.0] - 2026-10-29

### Added
- Explicit per-tool `approval_policy='ask'` with a local `approval_handler`, plus `'deny'` and `'allow'` choices. Omitting the policy preserves privileged-tool refusal, and nested tools retain their owner's policy.
- The `agent.toolify()` builder's `depends_on_tool(...)` accepts `result_scope`, matching the `depends_on_tool` decorator.

### Changed
- Requires maivn-contracts 0.1.3 for the tool approval policy.
- An `Agent` or `Swarm` created with `agent_id=` now runs, binds its resources and authors its skills under that id, the same id `serve()` already announced. Before, all of these used `name`, so memory stored for the `agent_id` was never recalled. Scopes without `agent_id` are unchanged. If you set an `agent_id` that differs from `name`, memory and resources saved earlier under the name are not visible to the new runs.

### Fixed
- `depends_on_await_for` is now enforced. The SDK sends each declaration with the invocation, and the platform holds the waiting tool until the referenced tool has completed successfully in the same run. A waiting call requested in the same batch as the referenced tool runs after it; otherwise the call is refused with a retryable `tool_await_for_pending` error and never reaches your code. With `timing='before'` the referenced tool waits for the declaring tool. `instance_control` pairs the nth waiting call with the nth completion (`'each'`), needs one completion (`'first'`), needs the latest requested call to have succeeded (`'last'`), or needs every requested call finished with at least one success (`'all'`). A tool the platform schedules itself waits silently instead of being refused. Requires a platform release with await-for support; an older server rejects the request with a 422 whose message says it is too old for `depends_on_await_for`.
- `depends_on_reevaluate` with `timing='after'` and `instance_control='first'` or `'last'` now sets a boundary: after the first referenced call, or after the last one in the turn.
- `depends_on_await_for` and `depends_on_reevaluate` references by function, method or model now resolve to the tool's registered name, so a tool registered with a custom `name=` matches. A reference that matches no tool of the declaring agent raises `ConfigurationError` before anything is sent, and so does one that names another agent's tool: each agent's tools run in their own run, so a control must reference a tool of the same agent.
- Controls declared on a delegate agent's tools or a swarm member's tools are now sent and enforced in the child run that executes those tools.
- `MCPServer(max_calls_per_minute=..., max_calls_per_day=...)` now limits tool calls. Over the minute limit a call waits for a free slot; over the daily limit it raises `MCPRuntimeError` without contacting the server. Both values must be at least 1.
- `mount_events(app)` no longer breaks the host app's OpenAPI schema; `/docs` and `/openapi.json` work with the events route mounted.

## [2.4.0] - 2026-10-28

### Added
- `Client.preview_redaction(...)` shows exactly what a run would send to the model provider before you run it, including covered picture regions; it waits up to 300 seconds by default.
- `approved_messages()` and `RunOptions.redaction_preview_id` run on an approved redaction, so the provider receives the copy you reviewed.
- A client-level default customer id for trusted backends.

### Changed
- Every httpx client reuses the shared TLS context.
- Requires maivn-contracts 0.1.2 for the redaction preview models.

## [2.3.0] - 2026-10-23

### Added
- Explicit bounded hosted jobs with detached start, durable status, cancellation and expense ceilings.
- Customer-scoped memory, resource and skill management through trusted backend customer assertions.
- Decision image inputs, `DecisionApp`, and decision-only runs through `decision_run`.

### Fixed
- Hardened event privacy, streamed cleanup, tool authorization and request validation.
- Require maivn-contracts 0.1.1 so clean installations include the contracts used by these APIs and their canonical schema data.

## [2.2.0] - 2026-10-09

### Added
- Expanded the SDK README decision reference with request/result contracts, routing and fallback errors, ordered/native batches, invocation stages and streaming, typed arguments, managed function/tool dependencies, candidate matching, categorized usage and privacy guidance.
- `InvokeResponse.privacy_guidance` exposes advisory over-redaction suggestions from the final event, including the delivered value. Guidance never changes redaction or unhides model input. It is omitted from the response representation to avoid incidental diagnostic output, but remains present in model/JSON serialization; do not log or persist it.
- `maivn.decisions`: categorical decisions with a caller-owned fallback (`DecisionClient`, fluent `decide(...)` builders, decision arguments, dependencies and candidate matching). Every primary decision goes through the mAIvn platform (`POST /v1/decisions`) with your normal mAIvn API key and `MAIVN_BASE_URL`. The platform picks the decision model from its signed catalog, shields explicitly marked private values in each request, and bills the call to your project. Results carry the served model and the platform's token receipt. There is no decision-model key and no model name to configure. `DecisionClient(fallback=..., config=..., authorize=..., client=None)` approves each request locally before anything is sent; only requests declared `privacy='public'` are sent. Values you mark private with `DecisionRequest(private_data={key: value})` are shielded into placeholders by the platform before the decision model sees them, and a placeholder verdict is resolved back locally.

### Fixed
- Declared private values inside cyclic event payloads cannot escape through history, replay, SSE or packet observers. Plain cyclic payloads remain unchanged.

## [2.1.0] - 2026-10-03

### Added
- `Client.astream` and `Agent.astream` accept `cancel_on_close=False`. Opting in cancels the accepted root invocation when the stream closes before a root final or error, including an interrupt awaiting input. Cancellation cleanup waits at most two seconds; failures preserve the original stream failure. The default retains disconnect and replay behavior.

### Fixed
- `EventBridge` no longer sends the raw value of a `PrivateData` to clients. SSE frames, `get_history()`, replays and `on_packet` observers now receive `{"__private__": true, "label": ..., "pii_type": ...}` in its place, for both audiences and at any depth, including inside dataclasses and pydantic models. Your own objects are left unchanged.

## [2.0.0] - 2026-09-28

The 2.x line targets mAIvn Platform v2. It is not a drop-in upgrade from 0.4.x; the client, configuration and CLI changed with the platform.

### Changed
- Runtime contracts come from the maivn-contracts package. maivn-shared is no longer used.
- Encrypted local storage for private data uses the private-data-vault extension (abi3 wheels for Python 3.10 and later).
- Python 3.10 is the minimum for the SDK. maivn-tools needs 3.12.
- Migration notes: see MIGRATION.md in this repository.

### Removed
- Runtime dependencies on docstring-parser, prompt-toolkit, python-dotenv and tzlocal.
- rich is now the optional extra maivn[rich].

## 0.4.3 and earlier

The 0.x history lives on the `v0.4.3` tag of this repository.
