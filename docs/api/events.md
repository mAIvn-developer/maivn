# Events

Public event contract for streaming SDK execution state into your backend and frontend.

> **Looking for a step-by-step guide with frontend client examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python, Rust, and more?** See [`guides/frontend-events.md`](../guides/frontend-events.md). This page is the API reference; the guide is the recipe book.

## Quick Start

**Tier 1 - Consume a stream (3 lines):**

```python
from maivn.events import normalize_stream

for event in normalize_stream(agent.stream(messages)):
    send_to_webhook(event.model_dump())
```

**Tier 2 - Stream events to a frontend (one-line FastAPI mount):**

```python
from fastapi import FastAPI
from maivn.events.fastapi import get_event_bridge, mount_events

app = FastAPI()
mount_events(app)  # → GET /maivn/events/{session_id}

@app.post("/start/{session_id}")
async def start(session_id: str):
    bridge = get_event_bridge(session_id)
    await bridge.emit_tool_event(tool_name="search", tool_id="t1", status="executing")
    await bridge.emit_tool_event(tool_name="search", tool_id="t1", status="completed", result={"ok": True})
    await bridge.emit_final("Done")
    return {"ok": True}
```

Install the optional extra to pull in `fastapi` + `sse-starlette`:

```bash
pip install "maivn[fastapi]"
```

**Tier 2 (lower-level) - Build an SSE endpoint by hand:**

```python
from maivn.events import EventBridge

bridge = EventBridge("session-1", audience="frontend_safe")
await bridge.emit_tool_event(tool_name="search", tool_id="t1", status="executing")
await bridge.emit_tool_event(tool_name="search", tool_id="t1", status="completed", result=data)
# In your SSE endpoint (pass last_event_id for reconnection support):
async for sse in bridge.generate_sse(last_event_id=request_last_event_id):
    yield sse
```

Use the lower-level path when your framework isn't FastAPI (Flask, raw ASGI, aiohttp, Django, …) — see the guide's [Other frameworks](../guides/frontend-events.md#other-frameworks) section.

## Overview

The `maivn.events` module provides a tiered API:

| Tier             | What                 | When to use                               |
| ---------------- | -------------------- | ----------------------------------------- |
| **1 - Stream**   | `normalize_stream()` | Webhooks, logging, analytics              |
| **1.5 - Replay** | `forward_normalized_event()` / `forward_normalized_stream()` | Backends that already normalized events and need to drive reporters and/or bridges |
| **2 - Bridge**   | `EventBridge`        | Building SSE/WebSocket frontend endpoints |
| **3 - Builders** | `build_*_payload()`  | Custom reporters, advanced integrations   |

Most developers only need Tier 1 or Tier 2.

## EventBridge audiences

Use the bridge audience to match the trust level of the frontend:

- `EventBridge(..., audience="frontend_safe")` for end-user browser frontends
- `EventBridge(..., audience="internal")` for trusted developer/admin frontends

`frontend_safe` preserves the mAIvn event contract but sanitizes sensitive bridge-bound values such as raw redaction details, injected private-data values, and internal error details.

## EventBridge constructor knobs

`EventBridge` accepts the following keyword arguments — most production deployments only need the first two:

| Argument | Default | Purpose |
|----------|---------|---------|
| `audience` | `"internal"` | `"frontend_safe"` redacts injected private data and error details before they hit history or the wire. Use it for end-user browsers. |
| `max_history` | `500` | Cap on the per-session history buffer. Reconnects with a stale `Last-Event-ID` past this point get a logged warning. |
| `heartbeat_interval` | `15.0` (seconds) | Idle interval between SSE keep-alives. Tighten when behind aggressive proxies (Cloudflare, AWS ALB). |
| `queue_maxsize` | `0` (unbounded) | Bounds the live queue so a slow consumer can't OOM the process. Pair with `backpressure`. |
| `backpressure` | `"block"` | `block` (await producer), `drop_oldest` (favor recency), `drop_newest` (favor order). |
| `schema_validation` | `"warn"` | `off` skips validation, `warn` logs malformed events, `strict` raises `EventSchemaError`. |
| `dedupe_interrupts` | `True` | Collapses repeated `interrupt_required` events by `(prompt, arg_name \| data_key)`. The reporter path and contract-stream replay can otherwise surface the same prompt twice. |
| `dedupe_status_messages` | `False` | When enabled, collapses an immediately-repeated `status_message` (same assistant + text). Opt in for apps that surface the same status through multiple reporting paths. |
| `reset_on_session_start` | `True` | Reset interrupt + status dedup state on the `session_start` packet. Disable for apps that drive turn boundaries through `reopen()` only. |

`EventBridge.generate_sse(...)` accepts a per-stream `heartbeat_interval` override (useful when one specific client lives behind a proxy with an aggressive idle timeout). Keep-alives are always emitted as SSE comment frames that browsers silently ignore — frontends do not need to subscribe to or filter a heartbeat event type.

## Dedup behavior

The bridge always dedupes `interrupt_required` events by `(prompt, arg_name | data_key)`. The reporter path and the contract-stream replay can otherwise surface the same prompt twice — without dedup, a frontend would render two prompts for one logical interrupt.

`status_message` dedup is opt-in (`dedupe_status_messages=True`) for apps that surface the same status through multiple reporting paths. Most apps emit deliberate status updates that can legitimately repeat, so the default is off.

Both dedup channels reset on `reopen()` and on the `session_start` packet (controlled by `reset_on_session_start`). Set the appropriate flags to `False` in tests and analytics scenarios that need to observe every emit.

## EventBridge normalization contract

`EventBridge` standardizes known mAIvn app-facing event families before they enter bridge history or SSE replay.

That normalization applies to both:

- typed helpers such as `emit_tool_event()` and `emit_enrichment()`
- raw `emit(event_name, payload)` calls for known event families

Known normalized families:

- `tool_event`
- `system_tool_start`
- `system_tool_chunk`
- `system_tool_complete`
- `assistant_chunk`
- `status_message`
- `interrupt_required`
- `agent_assignment`
- `enrichment`
- `final`
- `error`

For those event families, the bridge rebuilds packets through the shared mAIvn payload builders so the public envelope and nested descriptors stay consistent.

### Canonical identity behavior

The bridge also canonicalizes instance identity for the event families that drive long-lived UI state:

- `tool_event` and `system_tool_*` reuse a stable tool instance ID across start, progress, completion, and error packets when they describe the same logical runtime instance
- `agent_assignment` reuses a stable assignment ID for the same logical agent assignment timeline
- `enrichment` reuses a stable scope ID for the same logical scope

This makes it safer for frontends to key cards, timelines, and activity chips off nested descriptor IDs instead of trying to reconcile multiple transport-level IDs themselves.

### Known events vs custom events

Unknown/custom event names still pass through unchanged.

Use that passthrough behavior for domain-specific extensions. Use the typed helpers or shared payload builders for mAIvn contract events.

## Ownership model

Use one clear owner per concern:

1. Normalize raw SDK/server stream events once at your backend boundary with `normalize_stream()` or `normalize_stream_event()`.
2. If your backend then needs to drive a reporter and/or `EventBridge` from those normalized `AppEvent` values, use `forward_normalized_event()` or `forward_normalized_stream()`.
3. Let `EventBridge` own payload shaping for known app-facing event families, canonical ID reuse, audience-based sanitization, history buffering, and SSE replay.
4. If a frontend must still accept older/raw transport variants, normalize them once at the frontend ingress boundary into canonical bridge-facing packets before handing them to stores or reducers.
5. Keep app-local dedupe narrowly scoped to duplicate logical emissions caused by overlapping delivery paths in your app. Do not use app-local dedupe as a substitute for stream normalization.

Do not let two layers co-own the same event family. If live reporter callbacks already own `tool_event` or `status_message`, do not replay those same normalized events back through the reporter or bridge again. Pick one authoritative producer per family, then keep any remaining app-local dedupe as a narrow safety net.

This is the model used by mAIvn Studio: Studio inherits the shared contract, keeps any compatibility parsing at the frontend ingress edge, and adds only a thin dedupe layer for overlapping logical deliveries such as interrupts or repeated identical status messages within a turn.

## Recommended integration pattern

`Agent.stream()` and `Swarm.stream()` return raw `RawSSEEvent` values from the underlying SDK/server stream.

For most product integrations, you should normalize those raw events before forwarding them to your frontend:

```python
from maivn import Agent
from maivn.events import normalize_stream
from maivn.messages import HumanMessage

agent = Agent(name="support_agent", api_key="...")

raw_events = agent.stream(
    [HumanMessage(content="Summarize this ticket")],
    status_messages=True,
)

for event in normalize_stream(raw_events, default_agent_name="support_agent"):
    send_to_frontend(event.model_dump())
```

This gives your frontend a stable event shape with nested descriptors like `tool`, `assignment`, `assistant`, `enrichment`, `interrupt`, `output`, and `error_info` while preserving legacy flat fields for compatibility.

When your backend already knows deterministic tool-ID metadata, pass both `tool_name_map` and `tool_metadata_map` during normalization so nested agent invocations keep their canonical labels, agent typing, target agent IDs, and swarm scope.

If you already have normalized `AppEvent` values and want to forward them into a reporter or bridge, use the replay helpers instead of reinterpreting them in app code:

```python
from maivn.events import (
    EventBridge,
    NormalizedEventForwardingState,
    NormalizedStreamState,
    forward_normalized_event,
    normalize_stream_event,
)

bridge = EventBridge("session-1", audience="frontend_safe")
stream_state = NormalizedStreamState()
forwarding_state = NormalizedEventForwardingState()

for raw_event in agent.stream(messages, status_messages=True):
    normalized_events = normalize_stream_event(raw_event, state=stream_state)
    for event in normalized_events:
        await forward_normalized_event(
            event,
            bridge=bridge,
            state=forwarding_state,
        )
```

## Public models

### `RawSSEEvent`

Raw server event returned by `agent.stream()` and `swarm.stream()`.

```python
class RawSSEEvent(BaseModel):
    name: str                 # Event name from the SSE stream
    payload: Any = {}         # Decoded event payload; defaults to an empty dict
```

### `AppEvent`

Normalized app-facing event contract.

```python
class AppEvent(BaseModel):
    contract_version: str
    event_name: str
    event_kind: str | None
    scope: ScopeDescriptor | None
    participant: ParticipantDescriptor | None
    lifecycle: LifecycleDescriptor | None
    tool: ToolDescriptor | None
    assistant: AssistantDescriptor | None
    assignment: AssignmentDescriptor | None
    enrichment: EnrichmentDescriptor | None
    interrupt: InterruptDescriptor | None
    output: OutputDescriptor | None
    error_info: ErrorInfoDescriptor | None
    session: SessionDescriptor | None
    chunk: ChunkDescriptor | None
```

Nested descriptors use `extra="allow"` so the contract can evolve without breaking consumers that validate the normalized payloads.

## Normalization helpers

### `normalize_stream()`

Convert an iterable of raw SDK stream events into normalized `AppEvent` values.

```python
def normalize_stream(
    events: Iterable[RawSSEEvent],
    *,
    default_agent_name: str | None = None,
    default_swarm_name: str | None = None,
    default_participant_key: str | None = None,
    default_participant_name: str | None = None,
    default_participant_role: str | None = None,
    tool_name_map: dict[str, str] | None = None,
    tool_metadata_map: dict[str, dict[str, Any]] | None = None,
) -> Iterator[AppEvent]
```

Use the optional defaults when your application has stronger local context than the raw stream itself.

Examples:

- `default_agent_name` for single-agent apps
- `default_swarm_name` for root swarm dashboards
- `tool_name_map` when raw tool IDs need resolving to UI-facing names
- `tool_metadata_map` when raw tool IDs also need canonical type/scope metadata for nested agent and interrupt-aware UIs

### `normalize_stream_event()`

Normalize one raw event at a time.

```python
def normalize_stream_event(
    event: RawSSEEvent,
    *,
    state: NormalizedStreamState | None = None,
    default_agent_name: str | None = None,
    default_swarm_name: str | None = None,
    default_participant_key: str | None = None,
    default_participant_name: str | None = None,
    default_participant_role: str | None = None,
    tool_name_map: dict[str, str] | None = None,
    tool_metadata_map: dict[str, dict[str, Any]] | None = None,
) -> list[AppEvent]
```

Use this when your app already owns the outer stream loop and wants to normalize incrementally.

### `NormalizedStreamState`

State container used by `normalize_stream_event()` to preserve stream-local context such as accumulated assistant text and deferred model-tool completion.

## Replay helpers

### `forward_normalized_event()`

Replay one normalized `AppEvent` into a reporter and/or bridge.

```python
async def forward_normalized_event(
    event: AppEvent,
    *,
    reporter: Any | None = None,
    bridge: EventBridge | None = None,
    state: NormalizedEventForwardingState | None = None,
) -> NormalizedEventForwardingState
```

Use this when your backend already owns the outer stream loop, has normalized events, and wants the shared mAIvn forwarding logic for assistant streaming state, tool lifecycle routing, enrichments, interrupts, and terminal events.

### `forward_normalized_stream()`

Replay an iterable of normalized `AppEvent` values with shared forwarding state.

```python
async def forward_normalized_stream(
    events: Iterable[AppEvent],
    *,
    reporter: Any | None = None,
    bridge: EventBridge | None = None,
    state: NormalizedEventForwardingState | None = None,
) -> NormalizedEventForwardingState
```

### `NormalizedEventForwardingState`

Per-stream forwarding state used by the replay helpers to preserve assistant text accumulation, tool context, and reporter capability detection.

## Payload builders

Use the payload builders when your own backend emits or forwards events to a frontend and you want to produce the canonical mAIvn app-facing event contract directly.

Available builders:

- `build_tool_event_payload()`
- `build_assistant_chunk_payload()`
- `build_status_message_payload()`
- `build_agent_assignment_payload()`
- `build_enrichment_payload()`
- `build_interrupt_required_payload()`
- `build_final_payload()`
- `build_error_payload()`
- `build_session_start_payload()`
- `build_system_tool_start_payload()`
- `build_system_tool_chunk_payload()`
- `build_system_tool_complete_payload()`

## Event names

The module also re-exports commonly used raw event-name constants:

- `TOOL_EVENT_NAME`
- `UPDATE_EVENT_NAME`
- `PROGRESS_UPDATE_EVENT_NAME`
- `STATUS_MESSAGE_EVENT_NAME`
- `ENRICHMENT_EVENT_NAME`
- `INTERRUPT_REQUIRED_EVENT_NAME`
- `SYSTEM_TOOL_START_EVENT_NAME`
- `SYSTEM_TOOL_CHUNK_EVENT_NAME`
- `SYSTEM_TOOL_COMPLETE_EVENT_NAME`
- `SYSTEM_TOOL_ERROR_EVENT_NAME`
- `MODEL_TOOL_COMPLETE_EVENT_NAME`
- `FINAL_EVENT_NAME`
- `ERROR_EVENT_NAME`

## Backend-to-frontend guidance

For app integrations:

1. treat raw SDK stream events as transport/internal detail
2. normalize once at your backend boundary
3. forward already-normalized `AppEvent` values with `forward_normalized_event()` / `forward_normalized_stream()` when you need shared replay logic
4. send `AppEvent.model_dump()` payloads to the frontend
5. keep UI state keyed off normalized descriptors rather than raw event names alone
6. preserve `contract_version` in case you support multiple frontend versions
7. treat `interrupt` as part of the same public event contract, not as a side channel outside normalized events
8. emit tool completion from your reporter layer for **function tools** only - the normalizer handles system tool and model tool completion. In contract stream mode, use the functional tool name as `tool_id` so reporter completions match the normalizer's card identity
9. if you emit directly through `EventBridge`, prefer the typed helper methods; if you use raw `emit(...)`, restrict it to known normalized event families or explicit custom event extensions
10. choose `audience="frontend_safe"` for user-facing browsers and `audience="internal"` for trusted developer/admin frontends

## Custom event extensions

Apps may extend the contract with domain-specific events by using the same envelope structure. Set `event_kind` to `"custom"` and include `contract_version` and `event_name`:

```python
payload = {
    "contract_version": APP_EVENT_CONTRACT_VERSION,
    "event_name": "triage_card",
    "event_kind": "custom",
    "exception_id": "TEX-2026-0001",
    "report": {...},
}
```

This keeps app-specific events contract-aware so frontends can dispatch on the standard envelope fields.

Custom passthrough events are not rebuilt by `EventBridge`; only the known mAIvn contract families listed above are normalized automatically.

## Event deduplication and reconnection

`EventBridge.generate_sse()` accepts an optional `last_event_id` parameter. When provided, history replay skips all events up to and including the specified ID, following the standard SSE `Last-Event-ID` reconnection protocol. This is the recommended way to handle reconnects in multi-turn sessions:

```python
# SSE endpoint with reconnection support
@app.get("/stream/{session_id}")
async def stream(session_id: str, last_event_id: str | None = None):
    bridge = registry.get(session_id)
    return EventSourceResponse(bridge.generate_sse(last_event_id=last_event_id))
```

For multi-turn sessions, reconnect the SSE when sending a follow-up message and pass the last seen event ID. Between turns the HTTP connection may go stale (browser timeouts, proxy drops). See the [frontend events guide](../guides/frontend-events.md) for complete frontend integration examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python, Rust, .NET, and more.

Internally, the bridge also deduplicates events that exist in both the history buffer and the live queue during history replay.

If your application has two valid delivery paths that can surface the same logical event more than once, keep any extra dedupe in your app layer and scope it narrowly to that overlap. mAIvn Studio does this for logical interrupt duplicates and repeated identical status messages within a turn without changing the shared contract.

Frontend consumers should also dedupe by SSE event ID before dispatching events into application state. That is transport-level protection against replay or double delivery, not a replacement for logical event normalization.

## Compatibility

The normalized app-facing contract is versioned via `APP_EVENT_CONTRACT_VERSION`.

Current version:

```python
APP_EVENT_CONTRACT_VERSION == "v1"
```

The payloads intentionally preserve legacy flat fields alongside the normalized nested descriptors so existing consumers can migrate incrementally.
