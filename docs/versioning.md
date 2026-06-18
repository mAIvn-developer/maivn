# Versioning & Stability

mAIvn ships two version numbers, and they answer two different questions. One tells you which release of the SDK you have installed. The other tells event consumers — your backend, your frontend, your webhooks — which shape of streaming events to expect. This page explains both, what counts as stable versus experimental, and how we tell you when something is changing.

## What this is

When you build on top of any SDK, two things can move underneath you: the *code you import* and the *data that flows out of it*. mAIvn versions each of these separately so that a change to one never silently surprises consumers of the other.

- The **SDK version** (`maivn.__version__`) is the release of the `maivn` package on your machine. It follows semantic versioning, so the digits themselves tell you whether an upgrade is safe to take without reading code.
- The **event contract version** (`APP_EVENT_CONTRACT_VERSION`) describes the shape of the normalized events your application receives when it streams an agent's execution. It travels stamped onto every event, so a consumer can read it off the wire and decide how to handle the payload.

In plain terms: the SDK version is for *you*, the developer upgrading a dependency. The contract version is for *the systems that read your agent's output* — especially a frontend you do not redeploy in lockstep with your backend. The rest of this page makes both precise, with runnable examples.

## The SDK version (`__version__`) and what semver means for you

The installed release is available as a string on the top-level package and is part of the public surface:

```python
import maivn

print(maivn.__version__)  # e.g. "0.3.0"
```

It is also exposed through the CLI for quick diagnostics — useful in a bug report or a CI log:

```bash
maivn version
# maivn 0.3.0
```

The version is a plain string of the form `MAJOR.MINOR.PATCH`, and mAIvn follows [semantic versioning](https://semver.org/):

- **PATCH** (`0.3.0` → `0.3.1`) — backwards-compatible bug fixes. Safe to take without changing your code.
- **MINOR** (`0.3.0` → `0.4.0`) — new, backwards-compatible functionality. New decorators, new config options, new helpers. Your existing code keeps working.
- **MAJOR** (`0.x` → `1.0`) — changes that may require you to update your code.

> **Note**
> While the SDK is in the `0.x` series, the public surface is still settling. Treat minor (`0.x → 0.y`) bumps the way semver treats major bumps: read the changelog before you upgrade, and pin a version range you have tested against. Once the SDK reaches `1.0`, minor releases are additive-only.

A practical way to pin in `pyproject.toml` or `requirements.txt`:

```toml
# Accept patch and minor updates within the 0.3 line, but not 0.4.
maivn = ">=0.3.0,<0.4.0"
```

If you need to assert a minimum at runtime — for example, a library that depends on a feature added in a specific release — compare against the parsed version rather than the raw string:

```python
from importlib.metadata import version
from packaging.version import Version

if Version(version("maivn")) < Version("0.3.0"):
    raise RuntimeError("This integration requires maivn >= 0.3.0")
```

## The `AppEvent` contract version (`APP_EVENT_CONTRACT_VERSION`) and why it matters for event consumers

When you stream an agent's execution, the raw transport events are normalized into a stable, app-facing shape called `AppEvent` (see the [Events reference](api/events.md) and the [Frontend Events guide](guides/frontend-events.md)). Every normalized event carries the contract version it was produced under:

```python
from maivn import APP_EVENT_CONTRACT_VERSION

print(APP_EVENT_CONTRACT_VERSION)  # "v1"
```

The same constant is also importable from the events module, which is where most event-handling code lives:

```python
from maivn.events import APP_EVENT_CONTRACT_VERSION
```

Every `AppEvent` is stamped with it. You can read it off any event you receive:

```python
from maivn import Agent
from maivn.events import normalize_stream
from maivn.messages import HumanMessage

agent = Agent(name="assistant", api_key="your-api-key")

for event in normalize_stream(agent.stream([HumanMessage(content="Hello!")])):
    assert event.contract_version == "v1"
    send_to_frontend(event.model_dump())
```

**Why this is separate from the SDK version.** The SDK version changes when *you* upgrade a dependency. The contract version changes only when the *shape of the events* changes — and those two events rarely happen at the same time. The classic case is a frontend you ship independently of your backend: a browser app may run for weeks against a backend you have since upgraded. By keying on the contract version rather than the package version, that frontend can recognize which event shape it is being handed and adapt, without ever knowing which `maivn` release produced it.

**How the contract evolves without breaking you.** The contract is designed to grow additively:

- The nested descriptors on `AppEvent` (`tool`, `assistant`, `assignment`, `enrichment`, `interrupt`, `output`, `error_info`, and the rest) are declared with Pydantic's `extra="allow"`. New fields can appear on a descriptor without invalidating consumers that validate the payload — unknown fields are accepted, not rejected.
- Normalized payloads intentionally preserve the older flat fields alongside the newer nested descriptors, so a consumer written against an earlier shape can keep reading the fields it already knows while it migrates.
- Unknown or custom event names pass through normalization unchanged, which is the supported path for domain-specific event extensions (set `event_kind` to `"custom"` and include both `contract_version` and `event_name`).

A new *value* of `APP_EVENT_CONTRACT_VERSION` (for example, a future `"v2"`) is reserved for changes that are *not* purely additive — a rename or a removal that older consumers cannot absorb. Because the version rides on every event, you can support more than one frontend at a time by branching on it:

```python
def handle(event):
    if event.contract_version == "v1":
        render_v1(event)
    else:
        # A version you do not recognize: degrade gracefully rather than crash.
        log.warning("Unrecognized event contract version: %s", event.contract_version)
        render_best_effort(event)
```

> **Tip**
> Preserve `contract_version` end to end. When you forward `event.model_dump()` from your backend to your frontend, the field comes along for free. Keep it on the wire so the frontend can make the same decision the backend could.

This contract is the same one your own application routes expose when you mount the event stream. For example, `mount_events(app)` adds `GET /maivn/events/{session_id}` to *your* FastAPI app, and the events it serves carry the same `contract_version` your frontend can branch on:

```python
from fastapi import FastAPI
from maivn.events.fastapi import mount_events

app = FastAPI()
mount_events(app)  # -> GET /maivn/events/{session_id}
```

## Which surfaces are stable vs experimental

The boundary is simple and mechanical: **the public surface is what you can import from the top-level `maivn` package and the public `maivn_shared` package.** Everything you reach through those imports is covered by the semver promises above.

**Stable — import and rely on these:**

- The core classes: `Agent`, `Swarm`, `Client`, `ClientBuilder`, `BaseScope`, `MCPServer`, `MCPAutoSetup`, `ToolOverride`.
- The decorators: `tool_output`, `toolify`, `toolset`, `depends_on_tool`, `depends_on_agent`, `depends_on_private_data`, `depends_on_interrupt`, `depends_on_await_for`, `depends_on_reevaluate`, `compose_artifact_policy`.
- The public models from `maivn_shared` re-exported through `maivn`: `PrivateData`, `RedactedMessage`, the `*Config` models, permission and provider models, and so on. Result shapes like `SessionResponse` and `TokenUsage` are documented in [Shared Data Models](api/data-models.md).
- The events surface: `AppEvent`, `RawSSEEvent`, `EventBridge`, `normalize_stream`, the `build_*_payload` helpers, and `APP_EVENT_CONTRACT_VERSION`.
- Configuration, logging, scheduling, and interrupt helpers exported at the top level.
- `MaivnError` (imported from the public `maivn_shared` package) — the single, publicly importable base for every framework-raised exception.

> **Note**
> The authoritative list of the public surface is the package's `__all__`. If a name appears there (and on the per-class pages under [`api/`](api/README.md)), it is part of the supported interface. If it does not, treat it as internal.

**Internal — do not import or depend on these:**

Anything under the `maivn._internal` package is private. The single-underscore name is the signal: these are implementation details that can change, move, or disappear in *any* release — including patch releases — with no notice and no deprecation cycle. This holds even though some internal symbols are re-exported under their *public* import path; rely on the public path, never the `_internal` one.

A few error types are a deliberate, documented in-between case. Exceptions such as `ServerAuthenticationError`, `ToolExecutionError`, and `ToolNotFoundError` are *raised* during normal operation and are real things your code may encounter — but they are not re-exported from the top-level `maivn` package today. The stable way to handle them is to catch the public base class rather than importing the concrete type:

```python
from maivn import Agent
from maivn.messages import HumanMessage
from maivn_shared import MaivnError

agent = Agent(name="assistant", api_key="your-api-key")

try:
    response = agent.invoke([HumanMessage(content="Hello!")])
except MaivnError as exc:
    # Catches every framework-raised error uniformly, including types
    # that are not individually importable from the public package.
    log.error("mAIvn call failed: %s", exc.to_dict())
    raise
```

Catching `MaivnError` is forward-compatible: if one of those concrete types is promoted to the public surface later, your handler still works. Importing a concrete type from `maivn._internal` is not — that path can break at any release. See the [Troubleshooting page](troubleshooting.md) for the specific errors you are most likely to hit and how to resolve each one.

## How deprecations are communicated

mAIvn does not silently remove things from the public surface. When part of the supported interface is going away, it moves through a predictable sequence:

1. **Announcement.** A deprecation is called out in the release notes for the version that introduces it, with the replacement named and a migration note.
2. **A runtime warning where it applies.** Where a deprecated symbol still functions, calling it emits a standard Python `DeprecationWarning`. You can surface these in development and in CI:

   ```bash
   python -W error::DeprecationWarning your_app.py
   ```

   Treating deprecation warnings as errors in CI is the cheapest way to find out you are on a sunset path before the symbol is removed.
3. **A removal release.** The deprecated symbol is removed only in a release whose version bump reflects a breaking change — a major bump once the SDK is at `1.0`, or a minor (`0.x → 0.y`) bump while it is still in the `0.x` series. Removal never happens in a patch release.

The event contract follows the same spirit, expressed through the contract version: additive changes keep `APP_EVENT_CONTRACT_VERSION` the same and rely on `extra="allow"` plus preserved legacy fields, while a breaking change to the event shape bumps the version value so consumers can detect it on the wire. Because internal (`maivn._internal`) symbols are not part of the public surface, they are exempt from this process entirely — another reason to import only from the public package.

## Next steps

- **[Overview](overview.md)** — what mAIvn is, the one mental model, and your first agent.
- **[Core Concepts](core-concepts.md)** — the building blocks the public surface is made of.
- **[SDK Reference](api/README.md)** — the per-class home of the public surface; the `__all__` of every page.
- **[Events](api/events.md)** — the full `AppEvent` contract, normalization helpers, and payload builders.
- **[Frontend Events guide](guides/frontend-events.md)** — how to stream the contract to a browser and key UI state off `contract_version`.
- **[Errors & Exceptions](api/errors.md)** — the `MaivnError` hierarchy referenced above, its import path, and how to catch each type.
- **[Troubleshooting](troubleshooting.md)** — the concrete errors you may see and how to fix each one.
- **[Shared Data Models](api/data-models.md)** — the result and privacy model shapes (`SessionResponse`, `TokenUsage`, and more).
