# Scheduling

Reference for the scheduled invocation API. Both `Agent` and `Swarm`
inherit `cron()`, `every()`, and `at()` from `BaseScope`. Each returns a
chainable `CronInvocationBuilder` whose terminal methods (`invoke`,
`stream`, `batch`, `abatch`, `ainvoke`, `astream`) schedule the call and
return a `ScheduledJob` handle.

For end-to-end usage and patterns, see the
[Scheduled Invocation guide](../guides/scheduled-invocation.md).

## Import

```python
from maivn import (
    Agent,
    Swarm,
    JitterSpec,
    Retry,
    ScheduledJob,
    RunRecord,
    CronSchedule,
    IntervalSchedule,
    AtSchedule,
    list_jobs,
    stop_all_jobs,
)
```

## Entry Points

### cron()

```python
def cron(
    expression: str,
    *,
    tz: str | datetime.timezone | None = None,
    jitter: JitterSpec | timedelta | float | tuple | None = None,
    name: str | None = None,
    misfire: Literal['skip', 'fire_now', 'coalesce'] = 'coalesce',
    max_overlap: int = 1,
    overlap_policy: Literal['skip', 'queue', 'replace'] = 'skip',
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    max_runs: int | None = None,
    retry: Retry | None = None,
    emit_events: bool = False,
) -> CronInvocationBuilder
```

Parses `expression` with `croniter` and schedules the next-best fire time
in the given `tz` (default UTC). Supports standard 5-field cron, with
6-field (seconds) variants accepted by croniter.

The five fields are `minute hour day-of-month month day-of-week`. Useful
operators per field:

- `*` — every value
- `N` — exactly that value
- `A,B,C` — list of specific values
- `A-B` — inclusive range
- `*/N` — every Nth value, starting from the field minimum
  (e.g., `*/5` in the minute field = every minute divisible by 5)

For the full pattern reference, common recipes, time-zone / DST behavior,
and a cron preview helper, see [Scheduled Invocation →
Reading a cron expression](../guides/scheduled-invocation.md#reading-a-cron-expression).

### every()

```python
def every(
    interval: timedelta | float | int,
    *,
    tz: str | datetime.timezone | None = None,
    start: datetime | None = None,
    jitter: ... = None,
    # plus the same kwargs accepted by cron()
) -> CronInvocationBuilder
```

Fires at fixed intervals. A bare number is interpreted as seconds. The
first fire is at `start` (default `now`), with subsequent fires at whole
multiples of `interval`.

### at()

```python
def at(
    when: datetime,
    *,
    tz: str | datetime.timezone | None = None,
    jitter: ... = None,
    name: str | None = None,
    retry: Retry | None = None,
    emit_events: bool = False,
) -> CronInvocationBuilder
```

One-shot schedule. Internally `max_runs=1`. Use `jitter` if you want a
softened arrival time.

## CronInvocationBuilder

Chainable builder. Mutators return `self`; terminal methods return a
started `ScheduledJob`.

### Mutators

| Method | Purpose |
| --- | --- |
| `with_jitter(jitter)` | Replace the jitter spec |
| `with_retry(retry)` | Replace the retry policy |
| `with_overlap(policy, *, max_overlap=1)` | Configure concurrency policy |
| `with_misfire(policy)` | Configure misfire handling |
| `with_window(*, start_at=None, end_at=None)` | Bound the active window |
| `with_max_runs(n)` | Cap total fires (None = unbounded) |
| `with_emit_events(emit=True)` | Toggle EventBridge emission |

### Terminal methods

| Method | Effect on each fire |
| --- | --- |
| `invoke(*args, **kwargs)` | Sync `scope.invoke(...)` in a worker thread |
| `ainvoke(*args, **kwargs)` | Awaits `scope.ainvoke(...)` |
| `stream(*args, **kwargs)` | Drains `scope.stream(...)` to a list |
| `astream(*args, **kwargs)` | Drains `scope.astream(...)` to a list |
| `batch(inputs, **kwargs)` | Runs `scope.batch(inputs, ...)` |
| `abatch(inputs, **kwargs)` | Awaits `scope.abatch(inputs, ...)` |

Each terminal call **starts the job immediately** and returns a
`ScheduledJob`.

## JitterSpec

Bounded randomness applied to each fire so launches feel natural rather
than perfectly periodic.

```python
@dataclass(frozen=True)
class JitterSpec:
    min: timedelta = timedelta(0)
    max: timedelta = timedelta(0)
    distribution: Literal['uniform', 'normal', 'triangular'] = 'uniform'
    sigma: timedelta | None = None
    align_to: timedelta | None = None
    skip_if_overruns_next: bool = True
    seed: int | None = None
```

| Field | Description |
| --- | --- |
| `min`, `max` | Inclusive offset bounds. May be negative (fire earlier) and asymmetric. |
| `distribution` | `uniform`, `normal` (clamped to `[min,max]`), or `triangular` (peak at midpoint). |
| `sigma` | Std-dev for the normal distribution. Default ≈ `(max-min)/6`. |
| `align_to` | Optional snap-to-grid (e.g. 15-second resolution). |
| `skip_if_overruns_next` | If the sampled offset would push past the next scheduled time, skip the run instead of compressing. |
| `seed` | Deterministic RNG for tests. |

### Construction shortcuts

```python
JitterSpec.symmetric(timedelta(seconds=30))
# == JitterSpec(min=-30s, max=+30s)

JitterSpec.from_value(timedelta(seconds=10))
# == JitterSpec(min=-10s, max=+10s)

JitterSpec.from_value((0, timedelta(minutes=2)))
# == JitterSpec(min=0, max=+2m)
```

The `cron()` / `every()` / `at()` `jitter=` parameter accepts any of
those shorthand forms in addition to a `JitterSpec` instance.

### Sampling

```python
spec.sample() -> timedelta
spec.apply(scheduled_at, next_scheduled_at=None) -> tuple[datetime, timedelta, bool]
```

`apply` returns `(fire_at, offset, skipped)`. When `skipped` is True the
caller should drop the run.

## Retry

```python
@dataclass(frozen=True)
class Retry:
    max_attempts: int = 1
    backoff: Literal['constant', 'linear', 'exponential'] = 'constant'
    base: timedelta = timedelta(seconds=5)
    factor: float = 2.0
    max_delay: timedelta | None = timedelta(minutes=10)
    retry_on: tuple[type[BaseException], ...] = (Exception,)
```

`max_attempts` includes the initial try (so `1` disables retries). Delay
for attempt `n` (1-indexed):

- `constant`: always `base`
- `linear`: `base * (n - 1)`
- `exponential`: `base * factor ** (n - 2)`

`max_delay` caps the result. `retry_on` filters which exception types
trigger a retry; others are surfaced immediately.

## ScheduledJob

Handle returned by every terminal builder method. Lifecycle is driven by
an internal asyncio task running on the SDK's scheduler loop.

### Lifecycle

| Method | Description |
| --- | --- |
| `start()` | Idempotent. Called automatically by the builder. |
| `stop(*, drain=True, timeout=None)` | Stop scheduling. With `drain` waits for in-flight runs. |
| `pause()` | Block dispatch of *new* fires. In-flight runs still finish. |
| `resume()` | Resume from pause. |
| `trigger_now()` | Fire once immediately, outside the schedule. |

### Inspection

| Property / method | Description |
| --- | --- |
| `is_running`, `is_paused`, `is_done` | Lifecycle flags |
| `next_run_at` | Next scheduled fire (without jitter applied) |
| `next_runs(n=5)` | Up to `n` upcoming fires |
| `fire_count`, `success_count`, `failure_count`, `skip_count` | Counters |
| `last_run` | Last `RunRecord` or `None` |
| `history(*, limit=None)` | Past `RunRecord`s, oldest first |
| `events()` | Async iterator yielding completed `RunRecord`s |

### Callbacks

Each callback registration returns the job (chainable). Callbacks may be
sync or async; exceptions in callbacks are swallowed so they cannot
crash the scheduler.

```python
job.on_fire(callable)     # called when a run actually starts (post-jitter, post-overlap claim)
job.on_success(callable)  # status == 'succeeded'
job.on_error(callable)    # status == 'failed' (after retries exhausted)
job.on_skip(callable)     # status starts with 'skipped_'
```

`on_fire` does **not** fire for runs that were skipped (misfire, jitter
overrun, or overlap policy).

## RunRecord

```python
@dataclass
class RunRecord:
    scheduled_at: datetime
    fire_id: str
    fired_at: datetime | None = None
    finished_at: datetime | None = None
    jitter_offset: timedelta = timedelta(0)
    attempt: int = 1
    status: RunStatus = 'pending'
    result: Any = None
    error: BaseException | None = None
    metadata: dict[str, Any] = {}

    @property
    def duration -> timedelta | None  # finished_at - fired_at
```

`status` is one of: `pending`, `running`, `succeeded`, `failed`,
`skipped_misfire`, `skipped_jitter`, `skipped_overlap`, `cancelled`.

## Schedule classes

Used internally by `cron()` / `every()` / `at()` and exposed for
advanced cases (custom backends, testing, schedule preview):

| Class | Purpose |
| --- | --- |
| `CronSchedule(expression, tz=None)` | Cron-driven |
| `IntervalSchedule(interval, *, start=None, tz=None)` | Fixed interval |
| `AtSchedule(when, *, tz=None)` | One-shot |
| `Schedule` | Abstract base; subclass to provide custom timing |

All implement `next_after(after)` and `upcoming(n, *, after=None)`.

## Module-level helpers

```python
list_jobs() -> list[ScheduledJob]
stop_all_jobs(*, drain=True, timeout=None) -> None
```

The registry uses weak references so jobs that lose their last strong
reference are garbage-collected; keep a reference for as long as you
want the job to run.

## Misfire and overlap semantics

A fire is considered a **misfire** when, at the moment the loop attempts
to dispatch it, real time has already moved more than 30 seconds past
the scheduled time. Policy:

- `skip` — drop the run (`status = skipped_misfire`).
- `fire_now` — run immediately, ignoring the drift.
- `coalesce` (default) — run once now and discard any other missed
  fires for the same job.

**Overlap** controls what happens when the next fire arrives while a
prior run is still in flight. The relevant counter is the number of
in-flight invocations:

- `skip` (default) — drop the new fire (`status = skipped_overlap`).
- `queue` — wait for a slot.
- `replace` — accept the run; the prior run continues to completion in
  the background, and `record.metadata['replaced_token']` is set.

`max_overlap=0` disables the cap (unbounded concurrency).

## Timezone handling

`tz` accepts an IANA name (e.g. `"America/New_York"`) resolved through
`zoneinfo`, a `datetime.timezone` instance, or `None` for UTC.
DST transitions are handled by croniter for cron schedules. `IntervalSchedule`
uses absolute monotonic offsets from `start`, so DST does not shift it.
