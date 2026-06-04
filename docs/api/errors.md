# Errors & Exceptions

What the mAIvn SDK raises, when each error happens, how to handle it, and where to
import it from — grouped so you can tell a *fix-your-code* error from a *try-again*
error.

## What this is

When you call `agent.invoke(...)`, `swarm.invoke(...)`, or a `Client` method, the
SDK does three things in sequence:

1. **Validates** your code and inputs (did you configure the agent correctly?).
2. **Talks to the mAIvn service** over the network (authentication, the request,
   the response).
3. **Runs your tools locally** and synthesizes a result.

Each phase has its own failure modes, signaled with different exception types. The
sections below group the errors by phase.

> **One catch-all to remember.** Every error the framework defines subclasses
> `MaivnError`. If you only remember one thing, remember `except MaivnError`.

## Where SDK errors come from

There are three layers an error can surface from. Knowing the layer tells you
whether to retry, fix configuration, or fix your data.

### Validation errors (your code or inputs)

These are raised **before any network call**, while the SDK is wiring up your
agent, decorators, and invoke options. They are deterministic: the same input
always produces the same error. They are never transient, so retrying will not
help — you fix the call and run again.

Examples: constructing an `Agent` with no credentials, marking two tools as the
final tool, passing mutually-exclusive `invoke()` options, or decorating a class
that was never marked with `@maivn.toolset(...)`. Most of these are plain
`ValueError` / `TypeError` raised at construction or decoration time.

### Execution errors (during a run)

These happen **while a request is being processed** — a tool fails, a requested
tool cannot be found, or data cannot be parsed into the expected shape. These are
modeled as `MaivnError` subclasses so you can catch them by type.

### Transport errors (the service and the network)

These happen **at the boundary with the mAIvn service**: authentication is
rejected, the service rejects the request for a domain reason (for example, an
unavailable model), or the connection times out. Some of these are
mAIvn-specific; some are raw `httpx` / built-in exceptions that bubble up
unchanged.

> **Validation vs. execution vs. transport, in one line.** Validation =
> fix your code. Execution = something failed mid-run; inspect and decide.
> Transport = check credentials/connectivity, and retry only if the failure is
> transient.

## Per-exception reference

The publicly importable error types live in the `maivn_shared` package. A handful
of richer, more specific exceptions exist internally and are surfaced to you, but
are **not** part of the supported public import surface today — you catch those
through the `MaivnError` base. Each entry below calls out its import path.

### The public exception surface

```python
from maivn_shared import (
    MaivnError,           # base for every framework error
    ConfigurationError,   # invalid / missing configuration
    ValidationError,      # data validation failed
    SerializationError,   # serialize / deserialize failed
    HttpError,            # low-level HTTP transport error
    is_retryable,         # bool: is this exception worth retrying?
    wrap_exception,       # wrap a generic exception as a MaivnError
)
```

These five exception types, plus the two helpers, are the supported error surface.
Everything else you encounter is either a built-in Python exception
(`ValueError`, `TypeError`, `RuntimeError`), a raw `httpx` exception, or a
`MaivnError` subclass you catch through the base.

### MaivnError

The base exception for everything mAIvn-specific. Every SDK exception subclasses
it, so `except MaivnError` is the catch-all.

- **When it is raised:** not directly. It is the base and wrapper type — you catch
  it, you rarely raise it.
- **What it carries:** `message`, `error_code` (defaults to the class name),
  a `context` dict, and an optional `cause`. It also provides
  `.to_dict()` (a structured payload for logging or API responses) and
  `.with_context(**kwargs)` (returns a *new* exception with merged context —
  the original is left unchanged).
- **How to handle:** use it as the broad `except` around any `invoke` / `stream` /
  `Client` call when you want to treat all SDK errors uniformly and log a
  structured payload.

```python
from maivn import Agent
from maivn.messages import HumanMessage
from maivn_shared import MaivnError

agent = Agent(name='assistant', api_key='your-api-key')

try:
    response = agent.invoke([HumanMessage(content='Hello!')])
except MaivnError as exc:
    # Structured, log-friendly payload: error_type, error_code, message, context.
    logger.error('mAIvn request failed', extra=exc.to_dict())
    raise
```

**Import:** `from maivn_shared import MaivnError`

### ConfigurationError

Raised when configuration is invalid or missing — a required setting is absent, a
value is invalid, or a type does not match.

- **When it is raised:** during setup, when the SDK detects a configuration
  problem.
- **What it carries:** `message`, plus optional context (`setting`, `expected`,
  `actual`, `suggestion`).
- **How to handle:** treat as a setup mistake to surface to the developer or
  operator. **Never retry** — `is_retryable()` always returns `False` for this
  type.

> **Note.** The SDK also defines a narrower, same-named `ConfigurationError`
> internally for SDK-specific configuration problems. It is *not* publicly
> importable; catch it via the public `ConfigurationError` from `maivn_shared`,
> or via the `MaivnError` base.

**Import:** `from maivn_shared import ConfigurationError`

### ValidationError

Raised when data validation fails — invalid input data, a schema-validation
failure, or a constraint violation.

- **What it carries:** `message`, plus optional `field`, `value`, and `rule`
  context.
- **How to handle:** a non-retryable bad-input error. Fix the data and retry
  manually. `is_retryable()` always returns `False`.

> **Not the same as Pydantic's `ValidationError`.** This is the mAIvn shared
> validation error. If you also use Pydantic directly, be explicit about which one
> you are catching — import this one from `maivn_shared` and Pydantic's from
> `pydantic`.

**Import:** `from maivn_shared import ValidationError`

### SerializationError

Raised when serialization or deserialization fails — JSON encode/decode failures,
Pydantic model parsing failures, or data-format conversion errors.

- **What it carries:** `message`, plus optional `data_type` and `operation`
  (`'serialize'` or `'deserialize'`).
- **How to handle:** indicates a data-shape or contract mismatch. Log
  `.to_dict()` and report it; the `context` will tell you which type and
  operation were involved.

**Import:** `from maivn_shared import SerializationError`

### HttpError

A low-level HTTP error from the shared HTTP client.

- **What it carries:** `status_code: int | None`.
- **Note on the type hierarchy:** `HttpError` subclasses plain `Exception`, **not**
  `MaivnError`. Catching `MaivnError` will *not* catch it.
- **How to handle:** inspect `.status_code` to decide whether to retry or fail.

> **When you actually see this.** `HttpError` is raised by the shared HTTP client
> on request failures. The SDK's own `Client` request path does *not* raise it
> directly — it raises a server-authentication error on `401`/`403` and a
> `RuntimeError` on other `4xx` (see below). You catch `HttpError` when you use the
> shared HTTP client directly.

**Import:** `from maivn_shared import HttpError`

### Server authentication rejection

When the mAIvn service rejects a request because authentication is missing or
invalid, the SDK raises a mAIvn-specific authentication error (a `MaivnError`
subclass). It is raised eagerly when no API key is configured, and on any `401` or
`403` response from the service. It carries `status_code`, `url`, and helpful
`server_message` / `hint` fields.

- **How to handle:** check that an API key is available to your app (commonly via
  the `MAIVN_API_KEY` environment variable) and that it is passed to `Agent` or
  `Client`. **Do not retry** without fixing the credentials.

> **Import-path caveat.** This exception type is **not** re-exported from the
> public `maivn` package. Catch it through the publicly importable `MaivnError`
> base. Do not import it from an internal module path — that is not part of the
> supported public surface.

```python
from maivn import Agent
from maivn.messages import HumanMessage
from maivn_shared import MaivnError

agent = Agent(name='assistant', api_key='')  # missing key

try:
    agent.invoke([HumanMessage(content='Hello!')])
except MaivnError as exc:
    # error_code / context distinguish an auth failure from other MaivnErrors.
    print('Request failed:', exc.error_code, exc.message)
```

### RuntimeError (service rejected the request)

On a non-auth `4xx` response — for example, a request that names a model that is
not available to your account — the `Client` request path extracts the service's
human-readable reason and raises a plain Python `RuntimeError`:

```
RuntimeError: the mAIvn service rejected the request (422): <detail>
```

- **How to handle:** catch `RuntimeError` around `invoke` / `stream` / `Client`
  calls to surface the rejection reason. The message is actionable (it may, for
  example, list the models available to you). On `5xx`, the underlying
  `httpx.HTTPStatusError` is re-raised unchanged so you can distinguish a
  server-side failure from a request you can fix.

```python
import httpx
from maivn_shared import MaivnError

try:
    response = agent.invoke(messages)
except MaivnError:
    # Auth / framework errors.
    raise
except RuntimeError as exc:
    # Domain-level rejection short of auth — the message is the reason.
    print('Rejected:', exc)
except httpx.HTTPStatusError as exc:
    # 5xx from the service: retry with backoff, then escalate.
    print('Service error:', exc.response.status_code)
```

### Tool execution and lookup errors

Two runtime error types may surface while your tools run:

- **A tool execution failure** — raised when a tool's execution fails. Carries the
  tool identifier, a reason, and the original underlying error.
- **A tool-not-found error** — raised when a requested tool cannot be located.
  Carries the requested tool identifier and a preview of the available tools.

Both subclass `MaivnError`. **Neither is re-exported from the public `maivn`
package today** — catch them through the `MaivnError` base. Inspect `.error_code`
and `.context` (via `.to_dict()`) when you need to branch on which one occurred.

```python
from maivn_shared import MaivnError

try:
    response = agent.invoke(messages)
except MaivnError as exc:
    payload = exc.to_dict()
    # payload['error_code'] tells you which MaivnError subclass was raised;
    # payload['context'] carries tool_id, reason, available_tools, etc.
    logger.error('Run failed', extra=payload)
```

### Configuration and usage errors (raised before the network)

These are deterministic programming errors raised at construction, decoration, or
`invoke()`-validation time. They are surfaced as built-in `ValueError` /
`TypeError`, so catch those types directly. None of them are retryable. They cover
the same handful of mistakes you make while wiring an agent up — building an
`Agent` with no credentials, marking two tools as final, passing mutually-exclusive
`invoke()` options, decorating the wrong kind of object, binding a `depends_on_*`
`arg_name` that is not a real parameter, or passing a wrong-typed config model.

> **Why these are plain `ValueError` / `TypeError`.** They are programming
> errors, not runtime conditions — they always reproduce, so they read most
> naturally as standard Python exceptions with a fix hint in the message.

Each error message is self-describing and carries a fix hint inline. For the exact
error text, the cause, and a copy-pasteable before/after fix for each of these
cases, see the [Troubleshooting](../troubleshooting.md) page — it is the canonical
cookbook for these messages. This page stays at the concept level: *what kind* of
error it is and *how to handle it*, not a transcript of every string.

### Knowing whether to retry

The `is_retryable(exception)` helper answers one question: *is this error the
transient kind that might succeed on a second try?* It returns `False` for
`ConfigurationError` and `ValidationError` (those never get better on retry), and
`True` only for a small set of transient error names (connection and timeout-style
failures). Use it to gate a retry loop instead of hard-coding type checks.

```python
from maivn_shared import is_retryable

try:
    response = agent.invoke(messages)
except Exception as exc:
    if is_retryable(exc):
        ...  # back off and retry
    else:
        raise  # fix-your-input / fix-your-config error
```

`wrap_exception(original, wrapper_type=MaivnError, message=None, **context)` is the
companion helper: it wraps a generic exception as a `MaivnError` (preserving the
original as `.cause`), or — if the exception is already a `MaivnError` — returns it
unchanged, optionally with extra context merged in. Reach for it when you want a
uniform, structured error type across a boundary in your own code.

## Handling patterns

### A try/except around invoke() / stream()

A practical layered handler distinguishes the three failure layers. Order matters:
catch the most specific types first, then the `MaivnError` base, then transport.

```python
import httpx
from maivn import Agent
from maivn.messages import HumanMessage
from maivn_shared import MaivnError, is_retryable

agent = Agent(name='assistant', api_key='your-api-key')

try:
    response = agent.invoke([HumanMessage(content='Summarize the latest report.')])
    print(response.response)

except (ValueError, TypeError) as exc:
    # Validation / usage layer: a programming error. Fix the call; do not retry.
    print('Configuration or usage error:', exc)

except MaivnError as exc:
    # Execution / auth layer (tool failures, auth rejection, serialization, ...).
    logger.error('mAIvn error', extra=exc.to_dict())
    if is_retryable(exc):
        ...  # back off and retry
    else:
        raise

except RuntimeError as exc:
    # Domain-level service rejection short of auth (e.g. unavailable model).
    print('Service rejected the request:', exc)

except httpx.HTTPStatusError as exc:
    # 5xx transport failure: retry with backoff, then escalate.
    print('Service error:', exc.response.status_code)
```

Streaming follows the same shape — wrap the iteration, because an error can surface
as you consume the stream rather than at the call site:

```python
try:
    for event in agent.stream([HumanMessage(content='Walk me through it.')]):
        handle(event)
except MaivnError as exc:
    logger.error('Stream failed', extra=exc.to_dict())
    raise
```

### Reading SessionResponse.error and .status

Not every problem raises. A run can finish and report a failure *in the response
object* — for example, the orchestrator completed but the session itself ended in
a failed or interrupted state. `invoke()` returns a
[`SessionResponse`](data-models.md), which carries both a
`status` and an `error` field. Always check `status` before trusting the result.

`status` is one of: `"pending"`, `"running"`, `"waiting_for_tools"`,
`"completed"`, `"failed"`, or `"interrupted"` (or `None`). When a session fails,
`error` holds a human-readable message.

```python
response = agent.invoke([HumanMessage(content='Process the queue.')])

if response.status == 'failed':
    # The call did not raise, but the session ended in failure.
    print('Session failed:', response.error)

elif response.status == 'interrupted':
    # The run paused to collect input — handle the interrupt, do not treat as error.
    print('Session is waiting for input.')

elif response.status == 'completed':
    print(response.response)        # final assistant text
    print(response.result)          # structured / final-tool output, if any
    print(response.token_usage)     # per-session token usage, if available
```

> **Raised exception vs. failed status.** A raised exception means the SDK could
> not complete the round-trip (bad config, auth, transport, an unhandled tool
> failure). A `status == 'failed'` response means the round-trip completed and the
> session reported its own failure in `error`. Robust code checks both: wrap the
> call in `try/except`, *and* inspect `response.status` on success.

### Surfacing errors to a frontend

If you stream execution to a browser, errors belong in the event stream too. When
you build your own application route with the SDK's FastAPI adapter
(`mount_events(app)` mounts `GET /maivn/events/{session_id}`), use
`build_error_payload(...)` to emit a structured error event onto the per-session
bridge so the UI can render it cleanly. See the
[Frontend Events guide](../guides/frontend-events.md) for the full pattern.

## Next steps

- [SDK Reference](README.md) — the complete public import surface, grouped by area.
- [Overview](../overview.md) — what the SDK is and how a request flows through it.
- [Core Concepts](../core-concepts.md) — the vocabulary behind agents, tools, and sessions.
- [Troubleshooting](../troubleshooting.md) — worked before/after fixes for the most common configuration and usage errors.
- [Client reference](client.md) — credentials, timeouts, and the connection that transport errors come from.
- [Agent reference](agent.md) — the `invoke` / `stream` surface these errors are raised from.
- [Shared Data Models](data-models.md) — the full `SessionResponse`, `TokenUsage`, and related shapes.
- [Frontend Events guide](../guides/frontend-events.md) — stream errors to your own application route with `build_error_payload(...)`.
- [Versioning & Stability](../versioning.md) — why `MaivnError` is the forward-compatible base to catch, and which surfaces are stable.
