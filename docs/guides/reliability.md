# Timeouts, Retries & Reliability

Real systems are slow sometimes. A downstream API hangs, a network blip
drops a request, a tool takes longer than you expected. This guide is
about making your agents behave sensibly when that happens — failing
fast where waiting is pointless, waiting patiently where the work is
genuinely slow, and retrying the handful of failures that are worth
retrying.

## What this is

mAIvn gives you two layers of reliability control, and they solve
different problems:

- **Timeouts** put an upper bound on how long any single piece of work
  is allowed to take — one HTTP request, one tool call, one whole
  session. They protect you from hanging forever on something that is
  never going to finish.
- **Retries** automatically run a *scheduled* invocation again when it
  fails for a transient reason, with a backoff delay between attempts so
  you don't hammer a struggling dependency.

You configure timeouts on the `Client` (or `Agent`), so they apply to
every invocation. You configure retries per scheduled job, because
retrying only makes sense when something else is driving the schedule —
a cron expression, an interval, or a one-shot time.

The rest of this page shows the exact options, sensible defaults, and
how to catch and recover from the errors you'll actually see.

## Per-call and per-tool timeouts

Every invocation flows through a few distinct phases — the HTTP request
to the orchestrator, the execution of each of your tools, the resolution
of dependencies between tools, and the lifetime of the session as a
whole. mAIvn lets you bound each of those independently so one slow
phase can't quietly consume the budget meant for another.

You set these on the `Client`. When you pass an `api_key` directly to an
`Agent`, a `Client` is created for you, so the same options are
available there. Client-level values take precedence over the active
configuration and over the built-in defaults.

```python
from maivn import Client, Agent

client = Client(
    api_key='...',
    timeout=60,                     # HTTP request timeout (seconds)
    tool_execution_timeout=600,     # max time for each tool/LLM call
    dependency_wait_timeout=120,    # max wait for upstream dependencies
    total_execution_timeout=3600,   # max total session duration
)

agent = Agent(name='reporter', client=client)
```

### The four bounds

| Option | Default | What it bounds |
| --- | --- | --- |
| `timeout` | `600s` | A single HTTP request to the orchestrator. |
| `tool_execution_timeout` | `900s` (15 min) | One tool call (your function) or LLM call. |
| `dependency_wait_timeout` | `300s` (5 min) | How long a tool waits for the upstream tools it depends on. |
| `total_execution_timeout` | `7200s` (2 hours) | The whole session, end to end. May be `None` for no cap. |

A few things worth internalizing:

- **`timeout` is about the wire, the execution timeouts are about the
  work.** `timeout` is how long the SDK waits on a single HTTP response.
  The execution timeouts govern how long the actual reasoning-and-tools
  run is allowed to take. They are orthogonal — a fast network with a
  slow tool needs a generous `tool_execution_timeout`, not a generous
  `timeout`.
- **`dependency_wait_timeout` only matters when you chain tools.** If a
  tool is decorated with `@depends_on_tool(...)`, it can't run until its
  upstream finishes. This bound caps that wait. See the
  [Dependencies guide](dependencies.md) for the chaining model.
- **`total_execution_timeout` is your backstop.** Even if every
  individual phase stays under its own bound, a long enough chain of
  them can still run for hours. This is the single number that says
  "never longer than this, whatever happens."

> [!tip]
> Set `total_execution_timeout` for anything user-facing. A request that
> can run for two hours is almost never what a person waiting on a
> response actually wants, and a tighter total bound turns a silent
> hang into a prompt, catchable failure.

### Resolution order

When the SDK needs an effective timeout it resolves it top-down:

1. **Explicit value on the `Client` constructor** — what you passed in.
2. **Active `MaivnConfiguration`** — typically loaded from environment
   variables at process start.
3. **Built-in SDK default** — the values in the table above.

That means you can set sane defaults once via environment variables and
override just the one bound a particular workload needs in code.

### Configuring via environment

For production it's common to read configuration from the environment so
the same code runs everywhere with different limits. Each timeout has a
matching variable:

```bash
MAIVN_TIMEOUT=60
MAIVN_TOOL_EXECUTION_TIMEOUT=600
MAIVN_DEPENDENCY_WAIT_TIMEOUT=120
MAIVN_TOTAL_EXECUTION_TIMEOUT=3600
```

```python
from maivn import ClientBuilder

client = ClientBuilder.from_environment()
```

You can inspect the effective values at runtime, which is handy when a
timeout fires and you want to confirm what was actually in force:

```python
print(client.get_tool_execution_timeout())
print(client.get_dependency_wait_timeout())
print(client.get_total_execution_timeout())
```

See the [Configuration reference](../api/configuration.md) for the full
list of fields and variables, and the [Client reference](../api/client.md)
for the constructor signature.

### When a timeout fires

A request that exceeds its HTTP `timeout` surfaces as a transport-level
read timeout from the underlying HTTP client (`httpx.ReadTimeout`). Catch
it where you invoke, and either raise the limit or treat the failure as
recoverable:

```python
import httpx
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='reporter', api_key='...', timeout=30)

try:
    response = agent.invoke([HumanMessage(content='Summarize today.')])
except httpx.ReadTimeout:
    # Too slow for this budget. Raise the bound, queue for later, or
    # tell the user it timed out — but don't hang the request.
    response = None
```

## Retries and backoff for scheduled invocations

Retries in mAIvn are a property of a **scheduled** invocation, not of a
one-off `agent.invoke(...)` call. The idea is simple: when you've asked
an agent to run on a schedule, a single flaky fire shouldn't lose the
whole slot. You attach a `Retry` policy and the scheduler reruns the
fire — with a growing delay between attempts — before it gives up.

Pass a `Retry` to any schedule entry point (`cron`, `every`, `at`):

```python
from datetime import timedelta
from maivn import Agent, Retry
from maivn.messages import HumanMessage

briefer = Agent(name='Daily briefing', api_key='...')

job = briefer.cron(
    '23 9 * * MON-FRI',              # weekdays at 09:23
    tz='America/New_York',
    retry=Retry(
        max_attempts=4,              # initial attempt + 3 retries
        backoff='exponential',
        base=timedelta(seconds=2),
        factor=2.0,
        max_delay=timedelta(seconds=30),
        retry_on=(TimeoutError, ConnectionError),
    ),
).invoke([HumanMessage(content='Compose the daily ops briefing.')])
```

### The `Retry` policy

| Field | Default | Meaning |
| --- | --- | --- |
| `max_attempts` | `1` | Total attempts **including** the first. `1` disables retries. |
| `backoff` | `'constant'` | How the delay grows: `'constant'`, `'linear'`, or `'exponential'`. |
| `base` | `5s` | The base delay unit the backoff is built from. |
| `factor` | `2.0` | Multiplier for exponential backoff. |
| `max_delay` | `10 min` | Caps the computed delay so it can't grow without bound. |
| `retry_on` | `(Exception,)` | Which exception types trigger a retry; anything else surfaces immediately. |

The `backoff` field is typed by the public `RetryBackoff` alias (one of
`'constant'`, `'linear'`, `'exponential'`). The delay before attempt `n`
(1-indexed, so the first attempt has no delay) is:

- **constant** — always `base`
- **linear** — `base * (n - 1)`
- **exponential** — `base * factor ** (n - 2)`

In every case the result is clamped to `max_delay`.

> [!tip]
> Prefer **exponential** backoff for anything that talks to a shared
> downstream service. A constant retry every two seconds against a
> service that's struggling tends to make the struggle worse; an
> exponential delay backs off out of its way and is far more likely to
> let it recover.

### Retry only what's worth retrying

`retry_on` is the most important field for correctness. Retrying a
*transient* failure (a dropped connection, a timeout) is sensible.
Retrying a *deterministic* failure (bad input, a misconfiguration) just
wastes attempts and delays the inevitable — the second attempt will fail
exactly like the first.

Scope `retry_on` to the transient cases:

```python
retry = Retry(
    max_attempts=3,
    backoff='exponential',
    retry_on=(TimeoutError, ConnectionError),  # transient only
)
```

A `ConfigurationError` or `ValidationError` raised by the SDK is, by
design, never retryable — fixing the config or the data is the only path
forward. The `maivn_shared.is_retryable(exc)` helper encodes this: it
returns `False` for those two error types and `True` only for a small set
of transient error categories (connection, timeout, concurrency, and
temporary errors). You can use it to decide whether a failure is even a
candidate for a retry before you build a policy around it:

```python
from maivn_shared import is_retryable

if is_retryable(error):
    schedule_a_retry()
else:
    report_to_operator(error)
```

### How retries interact with a single fire

Retries happen **within a single fire** of a scheduled job. A flaky run
exhausts its attempts inside its own slot, so it never bleeds into the
next scheduled fire. Only the final outcome is recorded on the job's
`RunRecord`. If every attempt fails, the job's `on_error` callback fires
once, with the last exception:

```python
job.on_error(lambda record: alert(record.error))
```

> [!warning]
> A scheduled fire that exhausts its retries does **not** raise back into
> your code — the scheduler swallows it so it can keep running future
> fires. If you want to know about exhausted retries, you must wire
> `on_error`. A retry policy without an `on_error` handler fails
> silently.

Validation of the policy happens at construction, so mistakes are caught
immediately rather than at the first fire: `Retry` raises `ValueError`
for `max_attempts < 1`, a non-positive `factor`, or a negative `base`.

For the full scheduling surface — cron expressions, jitter, misfire and
overlap policies, and the `ScheduledJob` lifecycle — see the
[Scheduled Invocation guide](scheduled-invocation.md) and the
[Scheduling reference](../api/scheduling.md).

## Handling transient failures gracefully

Timeouts and retries reduce how often failures reach your code, but they
don't eliminate them. The final piece of reliability is catching what
does get through and responding deliberately.

The mechanics of *which* exceptions the SDK raises, where to import them
from, and how to build a layered `try/except` around `invoke` / `stream`
live in one place — the **[Errors & Exceptions reference](../api/errors.md)**.
This guide doesn't repeat that surface; instead, here is the reliability
lens on it.

Two facts carry most of the weight for reliability decisions:

- **`except MaivnError` is the single net.** Every framework-raised error
  subclasses `MaivnError`, including the runtime ones you can't import by
  name (tool failures, auth rejections). Lead with the broad catch and
  special-case the named public types only where the recovery differs.
- **Retry only the transient.** A `ConfigurationError` or `ValidationError`
  never gets better on a second try; the same input fails the same way.
  The `maivn_shared.is_retryable(exc)` helper encodes exactly that line —
  it returns `False` for those two and `True` only for a small set of
  transient categories (connection, timeout, concurrency, temporary). Gate
  any retry decision on it instead of hard-coding type checks:

```python
from maivn_shared import is_retryable

if is_retryable(error):
    schedule_a_retry()
else:
    report_to_operator(error)
```

When you log a failure, log the structured payload — `MaivnError.to_dict()`
preserves `error_code` and `context`, which beats parsing message strings.
For the per-exception table, the auth-vs-domain-rejection distinction, the
full layered handler, and reading a failed `SessionResponse.status`, see the
[Errors & Exceptions reference](../api/errors.md).

> [!note]
> If you stream agent activity to a browser, transient failures should
> reach the UI too, not just your logs — the same lifecycle stream that
> carries phase updates also carries error events. See the
> [Frontend Events guide](frontend-events.md) for the pattern.

### A reliability checklist

- **Bound the total.** Set `total_execution_timeout` on user-facing
  agents so a hang becomes a catchable failure.
- **Match the bound to the work.** Raise `tool_execution_timeout` for
  genuinely slow tools; keep `timeout` tight so a dead connection fails
  fast.
- **Retry only the transient.** Scope `retry_on` to timeouts and
  connection errors; never retry `ConfigurationError` or
  `ValidationError`.
- **Back off exponentially** against any shared downstream service.
- **Wire `on_error`** on every scheduled job — exhausted retries never
  raise back into your code.
- **Catch `MaivnError`** as the broad net, and special-case the named
  public types only where the recovery differs.
- **Log `.to_dict()`**, not the bare string, so you keep `error_code`
  and `context` for debugging.

## Next steps

- **[Scheduled Invocation](scheduled-invocation.md)** — the full retry,
  jitter, misfire, and overlap model for cron / interval / one-shot jobs.
- **[Dependencies](dependencies.md)** — how tool chaining works, which is
  what `dependency_wait_timeout` bounds.
- **[Errors & Exceptions](../api/errors.md)** — the canonical exception
  surface: every error type, where to import it, and the layered
  `try/except` patterns this guide builds on.
- **[Frontend Events](frontend-events.md)** — stream lifecycle and error
  events to a browser via `mount_events`.
- **[Troubleshooting](../troubleshooting.md)** — symptom-first fixes for
  the most common errors, including timeouts and connection failures.
- **[Configuration reference](../api/configuration.md)** — every timeout
  field and its environment variable.
- **[Client reference](../api/client.md)** — the constructor signature
  and timeout resolution methods.
- **[Scheduling reference](../api/scheduling.md)** — the `Retry`,
  `JitterSpec`, `ScheduledJob`, and `RunRecord` API.
- **[Core Concepts](../core-concepts.md)** and the
  **[Overview](../overview.md)** — the mental model the rest of the SDK
  builds on.
