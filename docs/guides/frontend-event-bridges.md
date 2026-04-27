# Frontend Event Bridges

Use `maivn.events.EventBridge` to stream SDK execution events to your frontend via SSE.

## Quick start

```python
from maivn.events import EventBridge

# Create a bridge for a user-facing frontend session
bridge = EventBridge("session-1", audience="frontend_safe")

# Emit events from your backend
await bridge.emit_tool_event(tool_name="search", tool_id="t1", status="executing")
await bridge.emit_enrichment(phase="planning", message="Planning actions...")
await bridge.emit_tool_event(tool_name="search", tool_id="t1", status="completed", result=data)
await bridge.emit_final("Here are the results.", result=structured_output)

# Expose as SSE endpoint (FastAPI example)
@app.get("/stream/{session_id}")
async def stream(session_id: str):
    bridge = get_bridge(session_id)
    return EventSourceResponse(bridge.generate_sse())
```

The bridge handles event history buffering, client reconnection replay, deduplication, and heartbeat keep-alive.

For mAIvn contract events, it also standardizes known event families before they enter bridge history or SSE replay.

## Bridge audiences

Choose the bridge audience explicitly based on who will read the stream:

```python
from maivn.events import EventBridge

# End-user frontend: sanitize sensitive event values before SSE/history
public_bridge = EventBridge("session-1", audience="frontend_safe")

# Internal/admin tooling: preserve the full event payload
internal_bridge = EventBridge("session-1", audience="internal")
```

Use `frontend_safe` for customer-facing browser sessions. Use `internal` for trusted developer/admin tools such as mAIvn Studio, Booth, or internal observability consoles.

## Architecture

```text
agent.stream() / swarm.stream()
-> maivn.events.normalize_stream(...)
-> maivn.events.forward_normalized_event(...)   # optional if replaying into bridge/reporter
-> EventBridge.emit(event_name, payload)
-> EventBridge.generate_sse()
-> SSE / WebSocket transport
-> frontend state store
-> UI components
```

## Single-owner flow

Use one owner per layer:

1. `normalize_stream()` / `normalize_stream_event()` owns raw transport normalization.
2. `forward_normalized_event()` / `forward_normalized_stream()` owns replaying normalized `AppEvent` values into reporters and bridges.
3. `EventBridge` owns canonical mAIvn payload shaping for known bridge families, stable identity reuse, replay-safe history, and audience-based sanitization.
4. Your frontend store owns UI state and rendering.

Do not let multiple layers co-own the same event family. If reporter callbacks already own live `tool_event` or `status_message` delivery, keep normalized replay for other families such as streamed assistant chunks or interrupts instead of forwarding the same logical event twice.

If your app has multiple valid delivery paths that can surface the same logical event twice, put any extra dedupe in the app layer and keep it narrow. Do not re-implement normalization or bridge identity logic in app code.

If you must support older/raw transport variants alongside the canonical bridge contract, normalize them once at your frontend ingress boundary before they hit reducers, stores, or UI handlers. Keep that compatibility shim at the edge; do not spread raw-shape parsing across per-event UI code.

## Managing multiple sessions

Use `BridgeRegistry` to manage bridges across sessions:

```python
from maivn.events import BridgeRegistry

registry = BridgeRegistry()
bridge = registry.create("session-1")
same = registry.get("session-1")
registry.remove("session-1")
```

## Read-only stream consumption

If you only need to consume events for logging, webhooks, or analytics without building a frontend, use `normalize_stream` directly:

```python
from maivn.events import normalize_stream

for event in normalize_stream(agent.stream(messages)):
    log_event(event.model_dump())
```

## What the normalized contract gives you

Each normalized event preserves a consistent top-level envelope:

- `contract_version`
- `event_name`
- `event_kind`
- `scope`
- `participant`
- `lifecycle`

Depending on the event, it also includes nested descriptors such as:

- `tool`
- `assistant`
- `assignment`
- `enrichment`
- `interrupt`
- `output`
- `error_info`
- `session`
- `chunk`

This makes it easier for frontends to key state off stable nested objects instead of a mix of raw event names and ad hoc flat fields.

## Known bridge event families

When you emit through `EventBridge`, the following event families are normalized through the shared mAIvn payload builders even if you call raw `emit(event_name, payload)` instead of a typed helper:

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

Unknown/custom event names still pass through unchanged.

For developer-facing backend code, this means you can treat the bridge as a predictable backend boundary for known mAIvn event families without re-implementing payload shaping or identity resolution in Studio, Booth, or similar frontends. App-specific dedupe can still sit on top when two valid delivery paths overlap.

## UI integration guidance

### Tool state

Use `tool.id` as the stable identifier for a tool card whenever available.

The bridge canonicalizes tool IDs for the same logical runtime instance across start, chunk, completion, and failure packets for normalized tool families.

Track:

- `tool.status`
- `tool.args`
- `tool.result`
- `tool.error`

### Assistant streaming

Append `assistant.delta` for live streaming text instead of trying to reconstruct it from raw `update` payloads yourself.

If you still ingest legacy `update` or `progress_update` events for compatibility, translate them into canonical `assistant_chunk` packets once at the stream boundary and let the rest of the frontend consume only `assistant.delta`.

### Agent assignments

Use `assignment.id` and `assignment.agent_name` to drive nested agent cards, multi-agent dashboards, or swarm execution timelines.

The bridge canonicalizes assignment IDs for the same logical assignment timeline so frontends can preserve one card per assignment instance.

If you also render agent-typed tool cards, prefer `tool_metadata_map` during normalization so deterministic invocation tool IDs retain canonical `tool.type`, `agent_name`, `swarm_name`, and target agent IDs.

### Enrichment

Treat enrichment as user-visible execution context, not as transport noise.

Use:

- `enrichment.phase`
- `enrichment.message`
- `scope`

This is especially useful for scope-aware UI components in swarm or nested-agent experiences.

The bridge canonicalizes enrichment scope IDs for the same logical scope so activity chips and progress trackers can reuse one stable scope identity.

### Interrupts

Drive input prompts from the nested `interrupt` descriptor.

Use:

- `interrupt.id`
- `interrupt.data_key`
- `interrupt.prompt`
- `interrupt.input_type`
- `interrupt.choices`

Treat interrupts as part of the same app-facing contract as tool, assignment, enrichment, and final-output events.

## Multi-turn SSE reconnection

For multi-turn sessions, the SSE connection may go stale between turns (browser timeouts, proxy drops). When sending a follow-up message, reconnect the `EventSource` to ensure event delivery.

`generate_sse()` accepts an optional `last_event_id` parameter that follows the standard SSE `Last-Event-ID` reconnection protocol. When provided, history replay skips all events up to and including the specified ID, so the client only receives events it has not seen:

```python
# Backend SSE endpoint (FastAPI example)
@app.get("/stream/{session_id}")
async def stream(session_id: str, last_event_id: str | None = None):
    bridge = get_bridge(session_id)
    return EventSourceResponse(bridge.generate_sse(last_event_id=last_event_id))
```

```typescript
// Frontend: track last event ID and pass on reconnect
let lastEventId: string | undefined;

function connect(sessionId: string) {
  const url = lastEventId
    ? `/stream/${sessionId}?last_event_id=${encodeURIComponent(lastEventId)}`
    : `/stream/${sessionId}`;
  const es = new EventSource(url);
  es.addEventListener("tool_event", (e) => {
    const payload = JSON.parse(e.data);
    lastEventId = payload.id;
    handleEvent(payload);
  });
}
```

## Snapshot and replay guidance

For reconnectable frontends, keep a session snapshot on your backend or client state layer and apply normalized events incrementally.

Recommended snapshot domains:

- assistant transcript
- tool cards
- agent assignments
- current enrichment state
- active interrupt
- final output
- error state
- token usage

Normalized events make replay safer because the app-facing shape is transport-agnostic.

## Versioning guidance

Always preserve `contract_version` when forwarding events to your frontend.

That allows you to:

- support gradual frontend migrations
- add new nested descriptors later
- distinguish between raw/internal payloads and public app-facing payloads

## Tool completion by type

Each tool type has a different completion flow. Understanding this prevents duplicate cards and ensures real-time status updates.

### Function tools

Raw SDK streams only provide `status="executing"` for function tools dispatched to the client. The reporter layer is responsible for emitting `status="completed"` and `status="failed"` after local execution finishes. In contract stream mode, completion events must use the functional tool name as the `tool_id` (matching the normalizer's card identity) rather than the server event ID.

### System tools

System tools (`web_search`, `repl`, `think`, and similar tools) have a complete server-side lifecycle: `system_tool_start`, `system_tool_chunk`, and `system_tool_complete` events all flow through the raw SSE stream. In contract stream mode, the normalizer handles the full lifecycle with consistent IDs derived from `assignment_id` or `tool_id`. Reporters should skip system tool events in contract stream mode to avoid duplicates.

### Model tools

Model tools (Pydantic structured outputs) are signaled via `model_tool_complete` from the server. The normalizer handles these and defers completion to the `final` payload. Reporters should skip model tool completion in contract stream mode.

### Summary

| Tool type | Contract stream: who emits start? | Contract stream: who emits complete? |
| --------- | --------------------------------- | ------------------------------------ |
| Function  | Normalizer                        | Reporter (with functional `tool_id`) |
| System    | Normalizer                        | Normalizer                           |
| Model     | Normalizer                        | Normalizer (deferred to `final`)     |

## When to use builders directly

Use payload builders when your backend is already observing execution state through hooks or reporters and you want to emit the canonical contract directly.

Examples:

- `build_tool_event_payload()`
- `build_enrichment_payload()`
- `build_interrupt_required_payload()`
- `build_final_payload()`
- `build_error_payload()`

## Event deduplication

When your bridge buffers event history for reconnection, the SSE generator must deduplicate events that exist in both the history buffer and the live queue. After replaying history, track replayed event IDs in a set and skip any queued events with matching IDs during the live streaming phase. This prevents duplicate cards during replay.

That transport-level replay dedupe is different from app-level logical dedupe. If your application can emit the same logical interrupt or status message through two already-valid paths, keep that suppression local to the app adapter. mAIvn Studio does this for logical interrupt duplicates and repeated identical status messages within a turn while still relying on the shared mAIvn bridge contract underneath.

On the frontend side, also dedupe by SSE event ID before dispatching into your state store. That protects against reconnect or transport-level double delivery without leaking dedupe concerns into per-event UI handlers.

## Custom event extensions

Apps can extend the contract with domain-specific events by including the standard envelope fields (`contract_version`, `event_name`, `event_kind`). Set `event_kind` to `"custom"` for app-specific events:

```python
payload = {
    "contract_version": APP_EVENT_CONTRACT_VERSION,
    "event_name": "triage_card",
    "event_kind": "custom",
    # ... domain-specific fields
}
```

Frontends can then dispatch on the standard `event_name` field while the envelope structure remains consistent with SDK contract events.

## Best practices

1. normalize raw events once, at the backend boundary
2. if you already have normalized `AppEvent` values, forward them with `forward_normalized_event()` / `forward_normalized_stream()` instead of reinterpreting them in app code
3. for trusted/internal frontends, forward normalized payloads unchanged; for end-user frontends, use `EventBridge(..., audience="frontend_safe")`
4. use nested descriptors as the primary UI contract
5. preserve `contract_version`
6. keep raw event parsing out of frontend components
7. treat `maivn.events` as the public integration surface, not `_internal`
8. emit tool completion from your reporter only for function tools - do not duplicate system or model tool completion that already comes from the normalized stream
9. deduplicate events when replaying history + streaming live events
10. if you need extra logical dedupe for overlapping delivery paths, keep it app-local and narrowly scoped
11. prefer typed `EventBridge` helpers in new code; if you use raw `emit(...)`, keep it to known normalized mAIvn event families or explicit custom events

## Related references

- `docs/api/events.md`
- `docs/api/agent.md`
- `docs/api/swarm.md`
- `docs/features/ENRICHMENT_EVENTS.md`
