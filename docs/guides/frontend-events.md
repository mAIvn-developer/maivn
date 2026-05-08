# Frontend Events

Stream live execution events from your maivn-powered backend to **any** frontend with one line of backend wiring and a standard `Server-Sent Events` consumer.

This guide is for SDK consumers. The backend (Python) and frontend (anything) examples are designed to be **easy** to adopt, **robust** under network conditions, and **dependable** across reconnects, restarts, and proxies.

---

## Contents

1. [Quickstart](#quickstart)
2. [What you get](#what-you-get)
3. [Wire format (protocol spec)](#wire-format)
4. [Backend recipes](#backend-recipes)
   - [FastAPI (one-liner)](#fastapi-one-liner)
   - [Authentication](#authentication)
   - [Custom bridge factory](#custom-bridge-factory)
   - [Other frameworks (Flask, raw ASGI)](#other-frameworks)
5. [Frontend client examples](#frontend-clients)
   - [JavaScript (browser)](#javascript-browser)
   - [TypeScript with reconnect](#typescript-reconnect)
   - [React hook](#react-hook)
   - [Svelte store](#svelte-store)
   - [Vue 3 composable](#vue-3-composable)
   - [Node.js](#nodejs)
   - [Swift (iOS / macOS)](#swift)
   - [Kotlin (Android)](#kotlin-android)
   - [Go](#go)
   - [Python](#python-client)
   - [.NET / C#](#dotnet-csharp)
   - [Rust](#rust)
   - [cURL (debugging)](#curl)
6. [Production hardening knobs](#production-knobs)
7. [Audiences and security](#audiences)
8. [Reconnection (Last-Event-ID)](#reconnection)
9. [Multi-turn sessions](#multi-turn)
10. [Testing your integration](#testing)
11. [FAQ](#faq)

---

## Quickstart

**Backend** — install the optional extra and mount the events router:

```bash
pip install "maivn[fastapi]"
```

```python
# main.py
from fastapi import FastAPI
from maivn.events.fastapi import get_event_bridge, mount_events

app = FastAPI()
mount_events(app)  # → GET /maivn/events/{session_id}

@app.post("/start/{session_id}")
async def start(session_id: str):
    bridge = get_event_bridge(session_id)
    await bridge.emit_status_message("orchestrator", "Working...")
    await bridge.emit_final("Done!")
    return {"ok": True}
```

**Frontend** — connect with any HTTP client that speaks SSE:

```html
<script>
  const sse = new EventSource("/maivn/events/session-123");
  sse.addEventListener("status_message", (e) => {
    const { data } = JSON.parse(e.data);
    console.log("status:", data.message);
  });
  sse.addEventListener("final", (e) => {
    sse.close();
    console.log("done:", JSON.parse(e.data).data.response);
  });
</script>
```

That's the whole loop. The rest of this guide explains how to harden it for production and how to write the same client in other languages.

---

## What you get

The `mount_events` helper wraps `EventBridge` and `BridgeRegistry` behind a FastAPI router and gives you, for free:

- **SSE streaming** with browser-native `EventSource` interop
- **Reconnect-safe replay** via the standard `Last-Event-ID` header / query parameter — clients see every event exactly once, even across drops
- **Comment-frame keep-alives** so frontends never need to subscribe to or filter a heartbeat event type
- **Bounded history + queue** with configurable backpressure (`block` / `drop_oldest` / `drop_newest`)
- **Schema validation** that catches malformed events before they hit the wire
- **Audience-based redaction** (`internal` vs `frontend_safe`) so you don't accidentally leak injected private data to the browser. The FastAPI helper creates `frontend_safe` bridges by default.
- **Per-session, per-process bridge registry** with explicit lifecycle (create / get / remove)
- **Optional auth hook** as a normal FastAPI dependency

If you outgrow `mount_events`, the lower-level `EventBridge` is fully public — you can wire it into Flask, raw ASGI, or any other server.

---

## Wire format

The backend speaks plain Server-Sent Events ([WHATWG spec](https://html.spec.whatwg.org/multipage/server-sent-events.html)). A typical frame looks like:

```
event: status_message
id: 7b1f5a92-f7e4-4d1a-9b37-5e1c2c2b7e7a
data: {"id":"7b1f5a92-...","type":"status_message","data":{"assistant_id":"orch","message":"Working..."},"timestamp":"2026-04-27T12:34:56+00:00"}

```

(blank line terminates the frame.)

### Fields

| SSE field | Required | Meaning |
|-----------|----------|---------|
| `event:` | yes | Event type (e.g. `status_message`, `tool_event`, `final`, `error`). Use this to dispatch on the client. |
| `id:` | yes | Unique event id. Browsers track this automatically as `Last-Event-ID` for reconnects. |
| `data:` | yes | Single-line JSON envelope (see below). |
| `:` (comment) | no | Keep-alive lines — clients silently ignore them. |

### Envelope

The `data:` JSON envelope always has this shape:

```json
{
  "id": "<uuid>",
  "type": "<event-type>",
  "timestamp": "<RFC3339 UTC>",
  "data": { /* type-specific payload */ }
}
```

The `data.data` field carries the type-specific payload. See [`docs/api/events.md`](../api/events.md) for the schema of each known type.

### Known event types (AppEvent v1)

| Event type | When emitted | Key payload fields |
|------------|--------------|-------------------|
| `session_start` | At the start of a turn | `session_id` |
| `assistant_chunk` | Streamed assistant text | `assistant_id`, `text` |
| `tool_event` | Function-tool lifecycle | `tool_id`, `tool_name`, `status`, `args`, `result`, `error` |
| `system_tool_start` / `system_tool_chunk` / `system_tool_complete` | System-tool lifecycle (`web_search`, `repl`, `think`, …) | `tool_id`, `tool_type`, `text`, `result` |
| `agent_assignment` | Swarm member status | `agent_name`, `assignment_id`, `status` |
| `enrichment` | Memory / planning context | `phase`, `message`, `scope_*` |
| `interrupt_required` | User input needed | `interrupt_id`, `data_key`, `prompt`, `input_type`, `choices` |
| `status_message` | Free-form status update | `assistant_id`, `message` |
| `final` | Terminal: turn complete | `response`, `result` |
| `error` | Terminal: turn failed | `error`, `details` |
| `session_end` | Terminal: connection close | — |

The bridge auto-closes after any **terminal** event (`final`, `error`, `session_end`) so clients can stop reading.

For multi-agent UIs, treat `agent_name` as a display label, not an identity key. A
supervised Swarm can redeploy the same agent name several times. Key cards by
`assignment_id`, `tool_id`, or the normalized nested descriptor ID. If a `tool_event`
and an `agent_assignment` are both reporting the same in-flight Swarm action with
different raw IDs, merge that lifecycle into one card; if the prior card is already
completed and a new `executing` event arrives for the same agent name, render a new
card.

### Custom event types

You can emit any custom event name via `bridge.emit("my_event", payload)`. Frontend dispatch keys off the type, so unknown types pass through unchanged. For end-user-facing bridges (`audience="frontend_safe"`), unknown types still get a generic injected-fields scrub — see [Audiences](#audiences).

---

## Backend recipes

### FastAPI (one-liner)

```python
from fastapi import FastAPI
from maivn.events.fastapi import mount_events

app = FastAPI()
mount_events(app)
# → GET /maivn/events/{session_id}
```

Customize prefix and path:

```python
mount_events(app, prefix="/api", path="/v1/sessions/{session_id}/events")
# → GET /api/v1/sessions/{session_id}/events
```

### Authentication

`mount_events(app, auth=...)` accepts any FastAPI dependency. Use it to enforce JWT / session / API-key auth:

```python
from fastapi import HTTPException, Request
from maivn.events.fastapi import mount_events

async def require_token(request: Request) -> None:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token or not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid token")

mount_events(app, auth=require_token)
```

Or compose with FastAPI's standard `Depends`:

```python
from fastapi import Depends
from maivn.events.fastapi import mount_events

async def authed_user(token: str = Depends(oauth2_scheme)) -> User:
    return await load_user(token)

async def auth_gate(user: User = Depends(authed_user)) -> None:
    # Reject if the user can't read this session.
    pass

mount_events(app, auth=auth_gate)
```

### Custom bridge factory

Subclass `EventBridge` when you need session-scoped state your app cares about:

```python
from maivn.events import EventBridge
from maivn.events.fastapi import mount_events

class MyBridge(EventBridge):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            session_id,
            audience="frontend_safe",
            schema_validation="strict",
            queue_maxsize=1024,
            backpressure="drop_oldest",
        )

mount_events(app, factory=MyBridge)
```

### Other frameworks

`mount_events` is FastAPI-specific. If you use Flask, raw ASGI, aiohttp, Django, or anything else, drop down to `EventBridge` directly — it produces the SSE stream and you pipe its output to whatever your framework expects.

**Flask + `flask-sse`-style example:**

```python
import json
from flask import Flask, Response
from maivn.events import EventBridge, BridgeRegistry

app = Flask(__name__)
registry = BridgeRegistry()

def render_sse(frame: dict) -> str:
    if "comment" in frame:
        return f": {frame['comment']}\n\n"
    parts = [f"event: {frame['event']}", f"id: {frame['id']}", f"data: {frame['data']}"]
    return "\n".join(parts) + "\n\n"

@app.route("/maivn/events/<session_id>")
def stream(session_id: str):
    bridge = registry.get(session_id) or registry.create(session_id)
    last_event_id = request.headers.get("Last-Event-ID")

    async def gen():
        async for frame in bridge.generate_sse(last_event_id=last_event_id):
            yield render_sse(frame)

    return Response(gen(), mimetype="text/event-stream")
```

The same approach works in **aiohttp** (`web.StreamResponse`), **Sanic**, **Quart**, **Starlette directly**, etc.

---

## Frontend clients

The wire format is the same in every language. Pick the snippet for your stack.

> **Why this guide is a recipe book, not an SDK:** frontends are diverse (web, mobile, desktop, CLIs, embedded). A universal wire protocol with thin per-language clients is more dependable than any single official package, which would inevitably lag one ecosystem or another. Copy the snippet you need; the wire format is stable.

### JavaScript (browser)

```html
<script>
  const sse = new EventSource("/maivn/events/session-123");

  sse.addEventListener("status_message", (e) => {
    const { data } = JSON.parse(e.data);
    console.log(data.assistant_id, data.message);
  });

  sse.addEventListener("tool_event", (e) => {
    const { data } = JSON.parse(e.data);
    console.log(data.tool_name, data.status, data.result);
  });

  sse.addEventListener("final", (e) => {
    const { data } = JSON.parse(e.data);
    console.log("done:", data.response);
    sse.close();
  });

  sse.addEventListener("error", (e) => {
    // Browser auto-reconnects on transient network errors.
    // The server emits `event: error` for terminal SDK errors.
    console.warn("connection error", e);
  });
</script>
```

The browser automatically resends the last event id as `Last-Event-ID` on reconnect — you do not need to track it yourself.

### TypeScript with reconnect

For non-browser environments (or when you want explicit control), wrap `EventSource` with reconnect, exponential backoff, and a typed event union:

```typescript
type MaivnEnvelope<T = Record<string, unknown>> = {
  id: string;
  type: string;
  timestamp: string;
  data: T;
};

type Handler = (event: MaivnEnvelope) => void;

interface ConnectOptions {
  url: string;
  lastEventId?: string;
  onEvent: Handler;
  onError?: (err: unknown) => void;
  signal?: AbortSignal;
}

const TERMINAL_TYPES = new Set(["final", "error", "session_end"]);

export function connectMaivnEvents(opts: ConnectOptions): () => void {
  let lastEventId = opts.lastEventId;
  let attempt = 0;
  let closed = false;
  let source: EventSource | null = null;

  const open = () => {
    if (closed) return;
    const url = new URL(opts.url, window.location.origin);
    if (lastEventId) url.searchParams.set("last_event_id", lastEventId);

    source = new EventSource(url.toString(), { withCredentials: true });

    const handle = (e: MessageEvent) => {
      try {
        const env = JSON.parse(e.data) as MaivnEnvelope;
        lastEventId = env.id;
        opts.onEvent(env);
        if (TERMINAL_TYPES.has(env.type)) close();
      } catch (err) {
        opts.onError?.(err);
      }
    };

    // Subscribe to every known type. Unknown types arrive on `message`.
    [
      "session_start", "assistant_chunk", "tool_event",
      "system_tool_start", "system_tool_chunk", "system_tool_complete",
      "agent_assignment", "enrichment", "interrupt_required",
      "status_message", "final", "error", "session_end",
    ].forEach(t => source!.addEventListener(t, handle));
    source.onmessage = handle; // catch-all for custom event types

    source.onopen = () => { attempt = 0; };
    source.onerror = (err) => {
      opts.onError?.(err);
      source?.close();
      // Exponential backoff: 0.5s, 1s, 2s, 4s, capped at 30s.
      const delay = Math.min(500 * 2 ** attempt++, 30_000);
      setTimeout(open, delay);
    };
  };

  const close = () => {
    closed = true;
    source?.close();
  };

  opts.signal?.addEventListener("abort", close);
  open();
  return close;
}
```

Use:

```typescript
const close = connectMaivnEvents({
  url: "/maivn/events/session-123",
  onEvent: (e) => console.log(e.type, e.data),
  onError: (err) => console.warn(err),
});
// later: close();
```

### React hook

```typescript
import { useEffect, useRef, useState } from "react";

export function useMaivnEvents(sessionId: string) {
  const [events, setEvents] = useState<MaivnEnvelope[]>([]);
  const lastEventId = useRef<string | undefined>(undefined);

  useEffect(() => {
    const close = connectMaivnEvents({
      url: `/maivn/events/${sessionId}`,
      lastEventId: lastEventId.current,
      onEvent: (e) => {
        lastEventId.current = e.id;
        setEvents((prev) => [...prev, e]);
      },
    });
    return close;
  }, [sessionId]);

  return events;
}
```

### Svelte store

```typescript
// $lib/stores/maivnEvents.ts
import { writable } from "svelte/store";

export function maivnEventStream(sessionId: string) {
  const store = writable<MaivnEnvelope[]>([]);
  const close = connectMaivnEvents({
    url: `/maivn/events/${sessionId}`,
    onEvent: (e) => store.update((prev) => [...prev, e]),
  });
  return { subscribe: store.subscribe, close };
}
```

```svelte
<script lang="ts">
  import { onDestroy } from "svelte";
  import { maivnEventStream } from "$lib/stores/maivnEvents";

  const stream = maivnEventStream("session-123");
  onDestroy(stream.close);
</script>

{#each $stream as event (event.id)}
  <div>{event.type}: {JSON.stringify(event.data)}</div>
{/each}
```

### Vue 3 composable

```typescript
// composables/useMaivnEvents.ts
import { ref, onUnmounted } from "vue";

export function useMaivnEvents(sessionId: string) {
  const events = ref<MaivnEnvelope[]>([]);
  const close = connectMaivnEvents({
    url: `/maivn/events/${sessionId}`,
    onEvent: (e) => { events.value.push(e); },
  });
  onUnmounted(close);
  return { events };
}
```

### Node.js

Node ≥ 18 ships `fetch` and streaming bodies. SSE on top of it:

```javascript
import { fetch } from "undici";

async function consume(url) {
  const res = await fetch(url, { headers: { Accept: "text/event-stream" } });
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message", id, data;

  for await (const chunk of res.body) {
    buffer += decoder.decode(chunk, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      event = "message"; id = undefined; data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith(":")) continue;          // comment
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("id:")) id = line.slice(3).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) {
        const env = JSON.parse(data);
        console.log(event, env);
        if (event === "final" || event === "error") return;
      }
    }
  }
}

await consume("http://localhost:8000/maivn/events/session-123");
```

Or use the [`eventsource`](https://www.npmjs.com/package/eventsource) package for an `EventSource`-compatible API in Node.

### Swift (iOS / macOS)

```swift
import Foundation

struct MaivnEnvelope: Decodable {
    let id: String
    let type: String
    let timestamp: String
    let data: [String: Any]  // decode with JSONSerialization or a typed model
}

final class MaivnEventClient: NSObject, URLSessionDataDelegate {
    private var task: URLSessionDataTask?
    private var buffer = Data()
    private let onEvent: (String, String, String) -> Void
    private var lastEventId: String?

    init(onEvent: @escaping (String, String, String) -> Void) {
        self.onEvent = onEvent
    }

    func connect(url: URL, lastEventId: String? = nil) {
        var request = URLRequest(url: url)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        if let id = lastEventId {
            request.setValue(id, forHTTPHeaderField: "Last-Event-ID")
        }
        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        task = session.dataTask(with: request)
        task?.resume()
    }

    func urlSession(_: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        guard let text = String(data: buffer, encoding: .utf8) else { return }
        let frames = text.components(separatedBy: "\n\n")
        for frame in frames.dropLast() where !frame.isEmpty {
            var event = "message", id = "", payload = ""
            for line in frame.components(separatedBy: "\n") {
                if line.hasPrefix(":") { continue }
                if line.hasPrefix("event:") { event = line.dropFirst(6).trimmingCharacters(in: .whitespaces) }
                else if line.hasPrefix("id:") { id = line.dropFirst(3).trimmingCharacters(in: .whitespaces) }
                else if line.hasPrefix("data:") { payload += line.dropFirst(5).trimmingCharacters(in: .whitespaces) }
            }
            if !payload.isEmpty { onEvent(event, id, payload) }
        }
        if let last = frames.last { buffer = last.data(using: .utf8) ?? Data() }
    }
}

let client = MaivnEventClient { type, id, jsonString in
    print("[\(type)] \(jsonString)")
}
client.connect(url: URL(string: "https://api.example.com/maivn/events/session-123")!)
```

For production iOS apps, consider [LDSwiftEventSource](https://github.com/launchdarkly/swift-eventsource) (MIT) which handles reconnect + Last-Event-ID automatically.

### Kotlin (Android)

Using OkHttp's `EventSource` extension:

```kotlin
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

val client = OkHttpClient()
val request = Request.Builder()
    .url("https://api.example.com/maivn/events/session-123")
    .header("Accept", "text/event-stream")
    .build()

val listener = object : EventSourceListener() {
    override fun onEvent(source: EventSource, id: String?, type: String?, data: String) {
        when (type) {
            "status_message" -> handleStatus(data)
            "tool_event" -> handleTool(data)
            "final", "error" -> source.cancel()
            else -> handleGeneric(type, data)
        }
    }

    override fun onFailure(source: EventSource, t: Throwable?, response: okhttp3.Response?) {
        // OkHttp does not auto-reconnect — schedule with WorkManager / coroutines.
    }
}

EventSources.createFactory(client).newEventSource(request, listener)
```

### Go

```go
package main

import (
    "bufio"
    "encoding/json"
    "fmt"
    "net/http"
    "strings"
)

type Envelope struct {
    ID        string                 `json:"id"`
    Type      string                 `json:"type"`
    Timestamp string                 `json:"timestamp"`
    Data      map[string]interface{} `json:"data"`
}

func consume(url string) error {
    req, _ := http.NewRequest("GET", url, nil)
    req.Header.Set("Accept", "text/event-stream")
    resp, err := http.DefaultClient.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    scanner := bufio.NewScanner(resp.Body)
    scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
    var event, id, data string

    for scanner.Scan() {
        line := scanner.Text()
        switch {
        case line == "":
            if data != "" {
                var env Envelope
                if err := json.Unmarshal([]byte(data), &env); err == nil {
                    fmt.Printf("[%s] %s\n", event, env.Data)
                    if event == "final" || event == "error" {
                        return nil
                    }
                }
            }
            event, id, data = "", "", ""
        case strings.HasPrefix(line, ":"):
            // comment / keepalive — ignore
        case strings.HasPrefix(line, "event:"):
            event = strings.TrimSpace(strings.TrimPrefix(line, "event:"))
        case strings.HasPrefix(line, "id:"):
            id = strings.TrimSpace(strings.TrimPrefix(line, "id:"))
        case strings.HasPrefix(line, "data:"):
            data += strings.TrimSpace(strings.TrimPrefix(line, "data:"))
        }
    }
    return scanner.Err()
}
```

### Python

For a Python frontend / CLI / test harness, use [`httpx-sse`](https://pypi.org/project/httpx-sse/) (BSD-3) for a clean async client:

```python
import httpx
from httpx_sse import aconnect_sse

async def consume(url: str, last_event_id: str | None = None) -> None:
    headers = {"Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id

    async with httpx.AsyncClient(timeout=None) as client:
        async with aconnect_sse(client, "GET", url, headers=headers) as event_source:
            async for sse in event_source.aiter_sse():
                if not sse.data:
                    continue
                envelope = sse.json()
                print(sse.event, envelope)
                if sse.event in ("final", "error", "session_end"):
                    return
```

### .NET / C#

```csharp
using System.Net.Http;
using System.Text.Json;

var http = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
using var req = new HttpRequestMessage(HttpMethod.Get,
    "https://api.example.com/maivn/events/session-123");
req.Headers.Accept.Add(new("text/event-stream"));

using var resp = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead);
resp.EnsureSuccessStatusCode();
using var stream = await resp.Content.ReadAsStreamAsync();
using var reader = new StreamReader(stream);

string evt = "message", id = "", data = "";
while (!reader.EndOfStream)
{
    var line = await reader.ReadLineAsync();
    if (line is null) break;
    if (line == "")
    {
        if (data.Length > 0)
        {
            var env = JsonDocument.Parse(data);
            Console.WriteLine($"[{evt}] {env.RootElement}");
            if (evt is "final" or "error") return;
        }
        evt = "message"; id = ""; data = "";
        continue;
    }
    if (line.StartsWith(":")) continue;
    if (line.StartsWith("event:")) evt = line[6..].Trim();
    else if (line.StartsWith("id:")) id = line[3..].Trim();
    else if (line.StartsWith("data:")) data += line[5..].Trim();
}
```

### Rust

```rust
// Cargo.toml: reqwest = { version = "0.12", features = ["stream"] }
//             futures-util = "0.3"
use futures_util::StreamExt;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let resp = reqwest::Client::new()
        .get("https://api.example.com/maivn/events/session-123")
        .header("Accept", "text/event-stream")
        .send().await?;

    let mut stream = resp.bytes_stream();
    let mut buf = String::new();

    while let Some(chunk) = stream.next().await {
        buf.push_str(std::str::from_utf8(&chunk?)?);
        while let Some(pos) = buf.find("\n\n") {
            let frame = buf.drain(..pos + 2).collect::<String>();
            let mut event = "message"; let mut data = String::new();
            for line in frame.lines() {
                if line.starts_with(':') { continue; }
                if let Some(v) = line.strip_prefix("event:") { event = v.trim(); }
                else if let Some(v) = line.strip_prefix("data:") { data.push_str(v.trim()); }
            }
            if !data.is_empty() {
                println!("[{event}] {data}");
                if event == "final" || event == "error" { return Ok(()); }
            }
        }
    }
    Ok(())
}
```

### cURL (debugging)

```bash
curl -N -H "Accept: text/event-stream" \
     http://localhost:8000/maivn/events/session-123
```

`-N` disables buffering so events arrive in real time. Add `-H "Last-Event-ID: <id>"` to test reconnect / replay.

---

## Production hardening knobs

The bridge has several constructor parameters worth tuning per environment.

```python
EventBridge(
    session_id,
    audience="frontend_safe",       # see § Audiences
    schema_validation="strict",     # off | warn | strict
    max_history=500,                # bound history buffer per session
    heartbeat_interval=15.0,        # seconds between keepalives
    queue_maxsize=0,                # 0 = unbounded; >0 enables backpressure
    backpressure="block",           # block | drop_oldest | drop_newest
)
```

| Knob | When to change | Notes |
|------|----------------|-------|
| `audience` | End-user browser → `frontend_safe`. Internal admin → `internal`. | Default is `internal`. |
| `schema_validation` | Set `"strict"` in tests/dev to fail fast on malformed events. Production usually wants `"warn"` (default). | `off` skips validation entirely. |
| `max_history` | Long agent loops with >500 events. | Older events are evicted; reconnects with a stale cursor get a logged warning. |
| `heartbeat_interval` | Tighten when behind aggressive proxies (Cloudflare, AWS ALB ≤30s idle). | Default 15s. |
| `queue_maxsize` + `backpressure` | High-throughput agents with potentially-slow consumers. | `block` is safest; `drop_oldest` favors recency; `drop_newest` favors order. |

### Per-stream overrides

You can override `heartbeat_interval` on a single SSE stream — useful if one specific client lives behind a tight proxy:

```python
async for frame in bridge.generate_sse(
    last_event_id=cursor,
    heartbeat_interval=5.0,    # this client only
):
    ...
```

The `mount_events` helper accepts the same keyword too.

---

## Audiences

`EventBridge(..., audience="frontend_safe")` enables redaction of fields the SDK marks as injected private data:

| Field | Behavior in `frontend_safe` |
|-------|----------------------------|
| `private_data_injected`, `interrupt_data_injected` | Replaced with a list of field names (no values) |
| `added_private_data`, `merged_private_data` | Values redacted to `<redacted>`; keys preserved |
| `matched_known_pii_values`, `unmatched_known_pii_values` | Values redacted to `<redacted>` |
| Top-level `error.details` | Cleared to `{}` |
| `error` strings | Run through `sanitize_user_facing_error_message()` |

**Default-deny for unknown event types:** if a custom event type lands on a `frontend_safe` bridge, the bridge logs a WARNING and runs a generic injected-fields scrub anyway. Add your custom type to the security policy if you need fine-grained handling.

Use `audience="internal"` for trusted developer/admin tools (mAIvn Studio, your own internal observability dashboards). Use `audience="frontend_safe"` for anything reaching an end user.

---

## Reconnection

The bridge implements the standard SSE reconnection protocol:

1. Each event has a unique `id` field.
2. Browsers automatically resend the most recent id as `Last-Event-ID` on reconnect.
3. The server replays history from the cursor onwards, skipping events the client already saw.
4. If the cursor is unknown (e.g. evicted from a 500-event buffer), the server logs a `WARNING` with the eviction count and replays whatever history it has — clients may see duplicates and should dedupe on `id` if this matters.

### Manual reconnect (non-browser)

For non-`EventSource` clients, send the cursor as a query parameter or `Last-Event-ID` header:

```bash
# query parameter (always works)
curl -N "http://api/maivn/events/sess?last_event_id=<id>"

# header (also works)
curl -N -H "Last-Event-ID: <id>" http://api/maivn/events/sess
```

The reference clients above show how to track and replay the cursor.

### Terminal events

When the server sends `final`, `error`, or `session_end`, the bridge auto-closes. Subsequent SSE frames are not produced. Clients should treat these as the end of the stream.

---

## Multi-turn sessions

A maivn session typically spans multiple user turns. The bridge has explicit lifecycle for this:

```python
bridge = get_event_bridge(session_id)
await bridge.emit_status_message("orch", "Working...")
await bridge.emit_final("Done")
# Bridge is now closed.

# Start the next turn:
bridge.reopen()
await bridge.emit_status_message("orch", "Working on follow-up...")
await bridge.emit_final("Done again")
```

`reopen()` clears history, the live queue, and identity-state aliases so the next turn starts clean. Subclasses can override to reset additional per-turn state.

The frontend's `EventSource` is unaffected by `reopen()` — it stays connected and starts seeing the new turn's events. You generally do **not** need to reconnect between turns.

---

## Testing your integration

For unit tests of code that emits to a bridge, construct one and inspect history:

```python
import pytest
from maivn.events import EventBridge

@pytest.mark.asyncio
async def test_my_agent_emits_status():
    bridge = EventBridge("test-session")
    await my_agent_run(bridge)
    types = [evt["type"] for evt in bridge.get_history()]
    assert types == ["session_start", "status_message", "final"]
```

For integration tests of the SSE endpoint, use FastAPI's `TestClient` or `httpx.AsyncClient` with an ASGI transport. The pattern is straightforward: spin up the FastAPI app with `mount_events`, fire a request that emits onto the bridge for a known `session_id`, and assert on the SSE frames the test client receives.

---

## FAQ

**Q: Do I need a database?**
No. The default bridge is fully in-memory. Sessions live in one process; reconnects against the same process replay full history.

**Q: What about multi-instance deployments?**
The bridge is process-local. If two backend replicas serve the same session through a load balancer, route the same `session_id` to the same replica (sticky sessions). For deployments that need cross-replica fan-out, persist events through your own message bus (Redis pub/sub, NATS, Postgres `LISTEN/NOTIFY`, etc.) and emit into per-replica bridges from a shared subscriber.

**Q: Will events survive a restart?**
No, by design. The bridge is a live stream, not a durable log. If you need cross-restart resume, persist events yourself in your application database and replay them when the client reconnects.

**Q: Can I use WebSockets instead of SSE?**
Yes — the bridge yields plain Python dicts. Pipe `bridge.generate_sse()` (or implement a thin equivalent) into your WebSocket framework's send loop. SSE is the recommended default because it works with any HTTP load balancer, doesn't need special framing, and `EventSource` reconnects come for free in browsers.

**Q: Can I emit binary data?**
SSE is text-only. Base64-encode binary payloads before emitting, or upload binaries via a separate HTTP endpoint and emit only the URL.

**Q: How do I expose a TypeScript SDK package?**
The SDK does not include a per-language client package — every frontend stack has its own conventions. Copy the [TypeScript with reconnect](#typescript-reconnect) snippet, adapt it to your project, and publish it as a private package if you want a reusable internal client. The wire format is stable across SDK versions.

**Q: My proxy keeps killing idle SSE connections.**
Tighten `heartbeat_interval` (e.g. `5.0`) — the default 15s is below most managed proxy timeouts but some are aggressive. Cloudflare's free tier idles at 100s, AWS ALB at 60s; pick `interval < idle_timeout / 3`.

---

## Related

- [Events](../api/events.md) — full event-name and payload reference
- [Best Practices](../best-practices.md) — broader SDK patterns
- [mAIvn Studio](maivn-studio.md) — local UI for inspecting live event streams while building agents
