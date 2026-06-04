# Enrichment Events and Streaming to a Frontend

Show your users what the agent is doing in real time — thinking, planning, calling a tool, and answering — instead of staring at a spinner. This guide is the canonical home for mAIvn's live event surface: the **enrichment** phase indicators that fill the gaps between tool calls and the final answer, plus everything you need to forward those events to a browser, mobile app, or any other frontend.

It is written for SDK consumers. Everything here is built on the public `maivn` and `maivn.events` interfaces — no internal endpoints, no private wire details. The wire protocol your frontend ultimately reads is Server-Sent Events (SSE), and the contract that travels over it is a small, versioned, public shape.

## Why It Matters

When an agent works on a request, several seconds can pass between "user hit send" and "the answer appears." Under the hood the agent is evaluating the request, planning actions, calling tools, and synthesizing a response. If your UI shows nothing during that window, it feels broken.

Enrichment events close that gap. They are short, fixed-label progress signals the runtime emits at the meaningful transitions of a turn — for example *evaluating the request*, *planning actions*, and *synthesizing the response*, plus memory and redaction phases when those subsystems run. Your frontend renders them as a live status line or a row of phase chips, so the user always sees forward motion.

Two things make them easy to consume:

- They arrive on the **same stream** as tool activity, assistant text, and the final answer — you read one stream, not several.
- They share **one normalized shape** (`AppEvent`), so a single handler can dispatch every event type your UI cares about.

> **Enrichment vs. status messages.** Enrichment phases are a fixed-label lifecycle signal that the SDK emits automatically. *Status messages* are a separate, opt-in channel of free-form dynamic text (see [Status Messages vs. Enrichment](#status-messages-vs-enrichment)). Keep them mentally distinct: phases say *which stage*, status messages say *what's happening right now in words*.

## Reading Events From a Stream

Every agent and swarm exposes a streaming surface. `agent.stream(...)` yields events as the turn unfolds, and `agent.astream(...)` is the async equivalent. Swarms expose the same `stream` / `astream` methods.

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='support_agent', api_key='...')

for event in agent.stream([HumanMessage(content='Summarize this ticket')]):
    print(event.name, event.payload)
```

Each yielded value is a `RawSSEEvent` — the transport-level event straight from the underlying stream:

```python
class RawSSEEvent(BaseModel):
    name: str                # event name, e.g. "enrichment", "tool_event", "final"
    payload: JsonValue = {}  # decoded JSON payload for that event
```

Raw events are fine for quick experiments, but for product code you almost always want the [normalized](#normalizing-raw-events) shape described below.

### Filtering with `events()`

`BaseScope.events(...)` (available on both `Agent` and `Swarm`) wraps an invocation with filtered event reporting. Use it when you want a callback fired for each event without writing your own stream loop:

```python
def on_event(event: dict) -> None:
    # Each dict has 'category', 'event' (the event name), and 'payload'.
    if event['event'] == 'enrichment':
        print('phase:', event['payload'].get('phase'))

response = agent.events(
    include=['enrichment', 'tool_event'],   # only these categories reach on_event
    on_event=on_event,
).invoke([HumanMessage(content='Look up the order status')])
```

Each dict passed to `on_event` has three keys: `category` (the event category used for filtering), `event` (the event name), and `payload` (the event's data — for enrichment, this carries `phase`, `message`, and any scope/memory/redaction/reevaluate fields).

The `events()` builder takes:

- `include` / `exclude` — select or drop event categories (a single string or a sequence).
- `on_event` — a callable invoked with each event dict.
- `auto_verbose` — defaults to `True`, which turns on verbose event reporting for the wrapped call unless you override it.

The builder is terminal on `.invoke(...)` / `.stream(...)`, so you choose blocking or streaming consumption at the call site.

## The Enrichment Contract

An enrichment event is just one event type on the stream. On the normalized contract it carries `event_kind == 'enrichment'` and an `enrichment` descriptor with two core fields:

| Field | Meaning |
| --- | --- |
| `phase` | A fixed label for the lifecycle stage (e.g. the evaluate, planning, and synthesis phases, memory phases, and the `reevaluate_accrued` cycle marker). Render it as a chip. |
| `message` | A short human-readable line for the current phase. Render it as the live status text. |

The phase labels are a small, fixed vocabulary the runtime owns — your UI keys behavior off `phase` and shows `message` as text. Treat unknown phases gracefully (display the `message`, skip any phase-specific styling) so your frontend stays forward-compatible as the vocabulary grows.

### Reevaluate cycles

When the agent decides to replan mid-turn, it emits an enrichment event with `phase == "reevaluate_accrued"`. This one carries extra attribution under a `reevaluate` bag so your UI can render replan cycles distinctly:

- `source` — `"dependency"` or `"llm"`, i.e. whether a tool dependency or the model itself triggered the replan.
- `trigger_tool` / `target_tool` — for dependency-driven cycles, which tool's result prompted re-planning of which target.
- `reevaluate_count` / `collected_count` — cycle and collected counts.

### Scope attribution (swarm cards)

In a multi-agent run, enrichment events can carry scope attribution so each chip lands on the correct agent or swarm card in a UI like mAIvn Studio:

- `scope_id` — stable identifier for the logical scope; key your cards off this.
- `scope_name` — display label.
- `scope_type` — e.g. agent vs. swarm.

For single-agent sessions these are omitted — there is only one scope. For swarms, prefer `scope_id` over `scope_name` as the identity key: a supervised swarm can deploy the same agent name more than once, and only the id is stable across deployments.

## Normalizing Raw Events

Raw stream events are a transport detail. For anything beyond a quick script, normalize them once at your backend boundary into the public `AppEvent` contract using `normalize_stream()`:

```python
from maivn import Agent
from maivn.events import normalize_stream
from maivn.messages import HumanMessage

agent = Agent(name='support_agent', api_key='...')

raw_events = agent.stream(
    [HumanMessage(content='Summarize this ticket')],
    status_messages=True,
)

for event in normalize_stream(raw_events, default_agent_name='support_agent'):
    send_to_frontend(event.model_dump())
```

`normalize_stream()` converts each `RawSSEEvent` into a stable, validated `AppEvent`. Pass the optional defaults (such as `default_agent_name` for single-agent apps or `default_swarm_name` for a root dashboard) when your application has stronger local context than the raw stream itself. `normalize_stream_event()` does the same thing one event at a time when your app owns the outer loop.

### The `AppEvent` shape

`AppEvent` gives your frontend one envelope with typed, optional descriptors for each concern:

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
    hook: HookDescriptor | None
```

For an enrichment event, `event_kind == 'enrichment'` and the `enrichment` descriptor is populated:

```python
class EnrichmentDescriptor(BaseModel):
    phase: str | None = None
    message: str | None = None
    memory: dict | None = None       # per-phase memory metrics, when present
    redaction: dict | None = None    # per-phase redaction metrics, when present
    reevaluate: dict | None = None   # attribution for reevaluate_accrued cycles
```

Descriptors use `extra="allow"`, so the contract can grow without breaking consumers that validate normalized payloads.

### Versioning

The normalized contract is versioned with `APP_EVENT_CONTRACT_VERSION`:

```python
from maivn.events import APP_EVENT_CONTRACT_VERSION

APP_EVENT_CONTRACT_VERSION == "v1"
```

Preserve `contract_version` when forwarding events so you can support multiple frontend versions during a migration. Payloads deliberately keep legacy flat fields alongside the nested descriptors so existing consumers can migrate incrementally.

### Building enrichment payloads directly

If your backend produces or forwards events itself (rather than only relaying the SDK stream), use `build_enrichment_payload()` to emit a canonical enrichment payload that matches what the SDK would have produced:

```python
from maivn.events import build_enrichment_payload

payload = build_enrichment_payload(
    phase='synthesizing',
    message='Synthesizing response...',
    scope_id='agent-research',
    scope_name='research',
    scope_type='agent',
)
```

There is a matching `build_*_payload()` for each event family (tool events, assistant chunks, status messages, agent assignments, interrupts, final, error, and the system-tool lifecycle). Reach for these when you own the producer side and want contract-shaped output without hand-assembling envelopes.

## Custom Reporters

Most apps consume events through `normalize_stream()` or the `events(on_event=...)` callback above — those are the supported public extension points, and they give you the full event stream in one place.

A custom "reporter" is just your own sink for those events. The cleanest pattern is a small dispatcher keyed off `event_kind` / `event_name`, with a dedicated branch for enrichment:

```python
from maivn import Agent
from maivn.events import normalize_stream
from maivn.messages import HumanMessage

class MyReporter:
    def report_enrichment(self, *, phase, message, scope_id=None, scope_name=None,
                          scope_type=None, memory=None, redaction=None, reevaluate=None):
        # Update your UI: chip for `phase`, status text for `message`.
        # `scope_*` route the chip to the right agent/swarm card.
        # `reevaluate` is populated when phase == "reevaluate_accrued".
        ...

    def dispatch(self, event) -> None:
        if event.event_kind == 'enrichment' and event.enrichment is not None:
            e = event.enrichment
            self.report_enrichment(
                phase=e.phase,
                message=e.message,
                scope_id=event.scope.id if event.scope else None,
                scope_name=event.scope.name if event.scope else None,
                scope_type=event.scope.type if event.scope else None,
                memory=e.memory,
                redaction=e.redaction,
                reevaluate=e.reevaluate,
            )

reporter = MyReporter()
agent = Agent(name='support_agent', api_key='...')
for event in normalize_stream(agent.stream([HumanMessage(content='Hi')])):
    reporter.dispatch(event)
```

The shape of `report_enrichment` mirrors the enrichment contract: a required `phase` and `message`, plus optional `scope_*`, `memory`, `redaction`, and reevaluate attribution kwargs. Implement only the branches your UI needs; everything else falls through.

## Status Messages vs. Enrichment

Status messages are a **separate, opt-in** channel. They are free-form dynamic text the orchestrator emits to describe what's happening in the moment — distinct from the fixed-label enrichment phases. They are off by default; turn them on per call:

```python
for event in agent.stream(messages, status_messages=True):
    ...
```

With `status_messages=True`, the stream also carries `status_message` events alongside everything else. Keep the two channels separate in your UI:

- **Enrichment phases** answer *which stage are we in* — render as chips or a stepper.
- **Status messages** answer *what is happening right now, in words* — render as a transient status line.

`Swarm.stream(...)` takes the same `status_messages` flag.

## Sending Events to a Browser

To get events into a frontend you put an `EventBridge` between your backend and the SSE connection. The bridge buffers history, validates payloads, handles reconnection replay, and — importantly — applies audience-based redaction so you do not accidentally leak injected private data to an end user.

### One-line FastAPI mount

If your backend is FastAPI, the `maivn.events.fastapi` adapter wires up the streaming endpoint in a single call. Install the optional extra:

```bash
pip install "maivn[fastapi]"
```

```python
from fastapi import FastAPI
from maivn.events.fastapi import get_event_bridge, mount_events

app = FastAPI()
mount_events(app)  # mounts GET /maivn/events/{session_id}; bridges are frontend_safe by default


# Your own endpoint kicks off a turn and emits mAIvn events onto the
# per-session bridge. Your frontend subscribes to the mounted route
# GET /maivn/events/{session_id} using the same session_id.
@app.post("/start/{session_id}")
async def start(session_id: str) -> dict:
    bridge = get_event_bridge(session_id)
    await bridge.emit_status_message('orchestrator', 'Working...')
    await bridge.emit_final('Done!')
    return {"ok": True}
```

`mount_events(app)` wires up the streaming surface for you; `get_event_bridge(session_id)` returns (creating if needed) the per-session bridge you emit onto. The adapter creates `frontend_safe` bridges by default because the typical consumer is an end-user browser.

You can pass an `auth` dependency to gate connections and a custom bridge `factory` if you need session-scoped state — both are standard FastAPI dependencies, so use your app's existing auth.

> **Other frameworks.** `mount_events` is FastAPI-specific. For Flask, aiohttp, Django, raw ASGI, or anything else, use `EventBridge` directly — it produces the SSE frames and you pipe them into your framework's streaming response. The same bridge object also yields plain dicts, so you can adapt it to WebSockets if you prefer.

### Audiences: `frontend_safe` vs. `internal`

The bridge's **audience** sets how much sensitive detail is allowed onto history and the wire:

```python
from maivn.events import EventBridge

# End-user browsers: redacts injected private data and internal error details.
bridge = EventBridge('session-1', audience='frontend_safe')

# Trusted developer/admin surfaces (e.g. your own observability dashboard).
internal_bridge = EventBridge('session-1', audience='internal')
```

`BridgeAudience` is a public type with two values:

- `frontend_safe` — preserves the mAIvn event contract but sanitizes sensitive bridge-bound values: injected private-data values are summarized to field names, redaction detail is masked, and internal error details are cleared. Unknown custom event types get a defensive injected-fields scrub so a stray emitter can't leak secrets. **Use this for anything reaching an end user.**
- `internal` — passes payloads through unredacted. **Use this only for trusted developer or admin tools.**

`EventBridgeSecurityPolicy` exposes the same audience model if you need to inspect or reuse the sanitization decision directly. `BridgeRegistry` manages per-session bridge lifecycle (create / get / remove) when you wire the bridge by hand.

### Reading on the frontend

Once events are flowing through a bridge, the frontend reads them as standard SSE. Each frame carries a JSON envelope; your client dispatches on the event type and updates state. The envelope is the normalized `AppEvent` shape, so enrichment, tool, assistant, and terminal events all share one structure — dedupe on the event id and key long-lived UI cards (tools, swarm members) off their stable descriptor ids rather than arrival order.

## Frontend Client Examples

The wire format is the same in every language, so the client is thin: open the stream, parse each SSE frame's JSON envelope, dispatch on the event type, and stop on a terminal event (`final`, `error`, `session_end`). Because every ecosystem has its own conventions, the SDK ships a stable wire contract rather than a per-language package — copy the pattern that fits your stack.

Here is the browser pattern with the native `EventSource` API:

```javascript
// Subscribe to the session's event stream.
const events = new EventSource(`/maivn/events/${sessionId}`);

events.onmessage = (e) => {
  const event = JSON.parse(e.data); // a normalized AppEvent envelope
  if (['final', 'error', 'session_end'].includes(event.event_name)) {
    events.close();                 // terminal event - stop listening
    return;
  }
  updateUI(event);                  // tool, assistant, enrichment, swarm, ...
};

events.onerror = () => events.close();
```

Any other stack (TypeScript with a typed event union; React, Svelte, or Vue bindings; Swift, Kotlin, Go, Rust, or .NET) follows the identical shape: open the SSE connection, JSON-parse each frame, dispatch on `event_name`, and dedupe by event id. For every event type and the payload fields each frame can carry, see the [Events API reference](../api/events.md).

## Next Steps

- [Events API reference](../api/events.md) — the full event-type and payload reference, plus normalization and replay helpers.
- [Sessions, Invocation, and Streaming](sessions-and-streaming.md) — how `invoke` / `stream` and sessions fit together.
- [Token and Usage](billing-and-usage.md) — read `TokenUsage` off responses to surface usage in your UI.
- [Private Data](private-data.md) — the redaction model behind `frontend_safe` audiences.
- [Multi-Agent](multi-agent.md) — swarms, scope attribution, and per-agent cards.
- [mAIvn Studio](maivn-studio.md) — a local UI for inspecting live event streams while you build.
- [Versioning & Stability](../versioning.md) — what `contract_version` means and how to branch on it so a frontend survives backend upgrades.
