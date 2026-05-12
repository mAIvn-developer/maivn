# Scheduled Invocation

Run any `Agent` or `Swarm` on a schedule — cron, fixed interval, or
one-shot — with optional jitter, retry, and overlap controls. The same
chain works with `invoke`, `stream`, `batch`, and the async variants
`ainvoke`, `astream`, `abatch`.

For the API reference, see [Scheduling](../api/scheduling.md).

## Quick start

```python
from datetime import timedelta
from maivn import Agent, JitterSpec, Retry
from maivn.messages import HumanMessage

briefer = Agent(
    name='Daily briefing',
    system_prompt='Compose the daily ops briefing in two sentences.',
    api_key='...',
)

job = briefer.cron(
    '0 9 * * MON-FRI',          # weekdays at 09:00
    tz='America/New_York',
    jitter=timedelta(minutes=10),  # ±10 minutes, uniform
    retry=Retry(max_attempts=3, backoff='exponential', base=timedelta(seconds=30)),
).invoke([HumanMessage(content='Compose the daily ops briefing.')])

job.on_fire(lambda r: print(f'fired at {r.fired_at}, jitter={r.jitter_offset}'))
job.on_error(lambda r: alert(r.error))
```

The job starts immediately. Keep a reference to `job` for as long as you
want it to run; the registry uses weak references.

## Choosing a schedule

`scope.cron(expression, ...)` — standard cron expressions, evaluated by
[croniter](https://github.com/kiorky/croniter):

```python
agent.cron('*/15 * * * *')              # every 15 minutes
agent.cron('0 0 1 * *')                 # midnight on the 1st
agent.cron('0 9 * * MON-FRI', tz='America/New_York')
```

### Reading a cron expression

A cron string is five space-separated fields. Each one bounds *when*
the schedule fires; the run only fires on a tick where **all five**
match. Read left-to-right as a list of permissions, not a list of
delays.

```text
 ┌──────────── minute        (0-59)
 │  ┌───────── hour          (0-23, 24-hour clock)
 │  │  ┌────── day of month  (1-31)
 │  │  │  ┌─── month         (1-12 or JAN-DEC)
 │  │  │  │  ┌ day of week   (0-6 or SUN-SAT, with 0 = Sunday)
 │  │  │  │  │
 *  *  *  *  *
```

Operators inside any field:

| Syntax    | Meaning                                          | Example       | Reads as                         |
| --------- | ------------------------------------------------ | ------------- | -------------------------------- |
| `*`       | every value in this field                        | `* * * * *`   | every minute, every hour, …      |
| `N`       | exactly that value                               | `0 9 * * *`   | 09:00 every day                  |
| `A,B,C`   | a list of specific values                        | `0 9,12,17 * * *` | 09:00, 12:00, and 17:00 daily |
| `A-B`     | inclusive range                                  | `0 9 * * MON-FRI` | weekdays at 09:00            |
| `*/N`     | every N units, starting from the field's minimum | `*/5 * * * *` | every 5 minutes (xx:00, xx:05, xx:10, …) |
| `A-B/N`   | every N units, but only inside the range         | `0 9-17/2 * * *` | 09:00, 11:00, 13:00, 15:00, 17:00 on every day |

> [!tip]
> `*/5 * * * *` does **not** mean "5 minutes after I save this job."
> It means "every minute whose number is divisible by 5" — so if you
> register it at 12:34:50, the next fire is 12:35, then 12:40, then
> 12:45. If you need an even-spacing wall-clock cadence relative to
> registration, use `scope.every(timedelta(minutes=5))` instead.

> [!warning]
> When both `day-of-month` and `day-of-week` are set to something
> other than `*`, croniter follows the cron tradition and treats them
> as **OR**, not AND. `0 9 1 * MON` fires on the 1st of every month
> *and* on every Monday — not just Mondays that fall on the 1st.

### Common patterns

```python
# Every-N cadence
agent.cron('* * * * *')                 # every minute
agent.cron('*/5 * * * *')               # every 5 minutes
agent.cron('*/15 * * * *')              # every 15 minutes
agent.cron('0 * * * *')                 # top of every hour
agent.cron('0 */6 * * *')               # every 6 hours (00:00, 06:00, 12:00, 18:00)

# Specific times of day
agent.cron('30 9 * * *')                # 09:30 daily
agent.cron('0 9,12,17 * * *')           # 09:00, 12:00, 17:00 daily
agent.cron('0 9 * * MON-FRI')           # 09:00 on weekdays
agent.cron('0 9 * * SAT,SUN')           # 09:00 on weekends

# Calendar-anchored
agent.cron('0 0 1 * *')                 # midnight on the 1st of every month
agent.cron('0 0 1 1 *')                 # midnight on Jan 1 (yearly)
agent.cron('0 9 15 * *')                # 09:00 on the 15th of every month

# Workday windows
agent.cron('*/10 9-17 * * MON-FRI')     # every 10 min, 09:00-17:50, weekdays
agent.cron('0 8-18 * * MON-FRI')        # top of every hour from 08:00 to 18:00, weekdays
```

### Pick a minute that isn't `0` or `30`

For "approximately every hour" or "every morning around 9," prefer
off-grid minutes like `7`, `23`, or `47`. Every system in the world
uses `0 * * * *` and `0 9 * * *`, so those instants get crowded with
unrelated traffic — your downstream APIs, observability, and rate
limits all see a synchronized spike. A few minutes of offset costs
nothing and meaningfully lowers contention:

```python
agent.cron('7 * * * *')                 # hourly, but on the :07
agent.cron('23 9 * * MON-FRI')          # weekdays at 09:23
```

When the user *does* mean a specific clock time ("09:00 on the dot
for the SLA report"), keep `0 9 * * *` — but make that an explicit
decision, not a default.

### Time zones and DST

The `tz=` argument is what most users actually want. Without it the
expression is evaluated in UTC, which silently drifts twice a year
relative to wall-clock business hours.

```python
agent.cron('0 9 * * MON-FRI', tz='America/New_York')
agent.cron('0 9 * * MON-FRI', tz='Europe/London')
```

DST transitions are handled by croniter:

- on the spring-forward boundary, a fire that would have landed in
  the skipped hour is shifted to the next valid minute
- on the fall-back boundary, the schedule does **not** double-fire
  during the repeated hour

If a job must run on real elapsed time regardless of DST, use
`scope.every(...)` instead of `cron(...)`.

### Six-field expressions (seconds)

croniter accepts an optional sixth field at the front for sub-minute
schedules. The SDK accepts the same form, but most production
schedules do not need it — fixed-interval `every()` is usually
clearer for sub-minute cadence.

```python
agent.cron('*/30 * * * * *')            # every 30 seconds (6-field)
agent.every(timedelta(seconds=30))      # equivalent, more readable
```

### Validating a cron expression

Use `CronSchedule(...).upcoming(n)` to preview the next `n` fire times
without registering a job — the fastest way to confirm an expression
does what you think before it goes live:

```python
from datetime import datetime, timezone
from maivn import CronSchedule

CronSchedule('0 9 * * MON-FRI', tz='America/New_York').upcoming(
    5,
    after=datetime(2026, 4, 27, tzinfo=timezone.utc),
)
```

`scope.every(interval, ...)` — fixed cadence aligned to a start time:

```python
from datetime import datetime, timedelta, timezone

agent.every(timedelta(minutes=5))  # every 5 minutes from now
agent.every(60, start=datetime(2026, 5, 1, tzinfo=timezone.utc))  # every minute from May 1
```

`scope.at(when, ...)` — one-shot; useful for "in two weeks, do X":

```python
from datetime import datetime, timedelta, timezone

when = datetime.now(timezone.utc) + timedelta(days=14)
agent.at(when).invoke(messages)
```

## Why jitter

Perfectly periodic launches are easy to fingerprint and easy to
overlap. Jitter spreads each fire over a small window so executions feel
natural and don't all hammer downstream systems on the same second.

```python
# Symmetric: ±30 seconds
agent.cron('*/5 * * * *', jitter=timedelta(seconds=30))

# Always-positive (more "human" arrival): 0–2 minutes after the hour
agent.cron('0 * * * *', jitter=JitterSpec(min=timedelta(0), max=timedelta(minutes=2)))

# Snap to a 15-second grid (looks like an operator clicked "run")
agent.cron(
    '0 9 * * *',
    jitter=JitterSpec(
        min=timedelta(0),
        max=timedelta(minutes=5),
        align_to=timedelta(seconds=15),
    ),
)

# Tightly clustered around the scheduled time using a normal distribution
agent.cron(
    '0 9 * * *',
    jitter=JitterSpec(
        min=-timedelta(seconds=20),
        max=timedelta(seconds=20),
        distribution='normal',
        sigma=timedelta(seconds=5),
    ),
)
```

`JitterSpec.skip_if_overruns_next` (default `True`) prevents jitter from
pushing a run past the next scheduled time. When that would happen the
run is dropped (`status = skipped_jitter`) instead of compressing the
gap.

For tests, set `seed=...` to make jitter deterministic.

## Async surface

The builder mirrors the underlying scope. Use the async terminals when
you're already inside an event loop:

```python
job = agent.cron('*/1 * * * *').ainvoke(messages)
job = agent.cron('*/1 * * * *').astream(messages)
job = swarm.cron('*/1 * * * *').abatch(many_inputs)
```

The schedule builder's `ainvoke` / `astream` / `abatch` are synchronous
factory methods that return a `ScheduledJob`; do not `await` them. Each
*fire* of the resulting job will execute via the matching async terminal
on the underlying `Agent` / `Swarm`.

`ainvoke` / `astream` are also available directly on `Agent` and
`Swarm` if you just want async execution without a schedule.

## Retry & backoff

```python
agent.cron(
    '*/10 * * * *',
    retry=Retry(
        max_attempts=4,                  # initial + 3 retries
        backoff='exponential',
        base=timedelta(seconds=2),
        factor=2.0,
        max_delay=timedelta(seconds=30),
        retry_on=(TimeoutError, ConnectionError),
    ),
).invoke(messages)
```

Retries happen *within a single fire*, so a flaky run doesn't burn the
next slot. Only the final outcome is recorded on the `RunRecord`. If
all attempts fail, `on_error` fires once with the last exception.

## Misfire and overlap

If the scheduler wakes up to a fire that's already late by more than 30
seconds (process paused, system suspended, etc.), the misfire policy
decides what to do:

| Policy | Behavior |
| --- | --- |
| `coalesce` (default) | Run once now; discard duplicates. |
| `skip` | Drop the missed fire entirely. |
| `fire_now` | Run immediately, ignoring drift. |

If a new fire arrives while a previous run is still in flight, the
overlap policy applies:

| Policy | Behavior |
| --- | --- |
| `skip` (default) | Drop the new fire (`status = skipped_overlap`). |
| `queue` | Wait for a free slot. |
| `replace` | Run anyway; tag the new run with `metadata['replaced_token']`. |

`max_overlap` caps the number of concurrent in-flight runs (default
`1`). Set to `0` for unbounded.

## Inspection and lifecycle

```python
job = agent.cron('*/5 * * * *').invoke(messages)

job.next_run_at                  # datetime | None
job.next_runs(5)                 # next 5 scheduled times
job.fire_count                   # successful + failed + skipped
job.last_run                     # most recent RunRecord
for record in job.history():     # all RunRecords (oldest first)
    print(record.status, record.attempt, record.duration)

job.pause()      # stop scheduling new fires
job.resume()
job.trigger_now()  # fire once now, outside the schedule

job.stop(drain=True, timeout=30)  # graceful shutdown
```

Use `events()` for a streaming view in async code:

```python
async for record in job.events():
    log.info('fire %s -> %s', record.fire_id, record.status)
```

## Production checklist

- **Pin the timezone**: `tz='America/New_York'` (or whatever) — never
  rely on the host's local clock for scheduled work.
- **Set `max_runs` or `end_at`** for staged rollouts so the job has a
  natural end.
- **Always pick an overlap policy that matches your tool** — agents
  that hold non-idempotent state want `skip`; pure read jobs may use
  `replace`.
- **Use jitter** for any fan-out larger than a handful of jobs to avoid
  coordinated load on downstream systems.
- **Wire `on_error`** to your alerting; a fire that exhausted retries
  will not raise back into your code.
- **Cap concurrency**: leave `max_overlap=1` unless the workload is
  proven safe to interleave.
- **Stop on shutdown**: call `job.stop()` (or `stop_all_jobs()`) from
  your process shutdown hook so in-flight runs drain.

## Testing

`JitterSpec(seed=...)` makes randomness reproducible. For schedule
inspection without actually firing, use the schedule classes directly:

```python
from maivn import CronSchedule
from datetime import datetime, timezone

CronSchedule('*/15 * * * *').upcoming(4, after=datetime(2026, 1, 1, tzinfo=timezone.utc))
# -> [00:00, 00:15, 00:30, 00:45]
```

To exercise the full pipeline in unit tests, drive a stub scope through
`CronInvocationBuilder` directly with an `IntervalSchedule` of a few
hundred milliseconds.

## Studio

mAIvn Studio surfaces the same configuration on every app's
**Schedule** tab — cron expression, jitter range and distribution,
misfire/overlap policy, retry, and a live runs table. Configurations
made in Studio call directly into the SDK; the underlying
`ScheduledJob` is the same handle you'd get in code.

The runs table is driven by the SDK's lifecycle callbacks
(`on_fire`, `on_success`, `on_error`, `on_skip`), pushed straight
to the browser over SSE. A new run's card appears the moment
`on_fire` runs server-side — no polling delay between the countdown
hitting zero and the card showing the live message, tool cards, and
enrichment chips. The status pill flips from running to
succeeded / failed / skipped as soon as the matching terminal
callback fires.
