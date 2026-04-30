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

`scope.every(interval, ...)` — fixed cadence aligned to a start time:

```python
agent.every(timedelta(minutes=5))                       # every 5 minutes from now
agent.every(60, start=datetime(2026, 5, 1, tzinfo=UTC)) # every minute from May 1
```

`scope.at(when, ...)` — one-shot; useful for "in two weeks, do X":

```python
when = datetime.now(UTC) + timedelta(days=14)
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
job = await_for_setup_then(agent.cron('*/1 * * * *').ainvoke(messages))
job = agent.cron('*/1 * * * *').astream(messages)
job = swarm.cron('*/1 * * * *').abatch(many_inputs)
```

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

mAIvn Studio surfaces the same configuration on every demo's
**Schedule** tab — cron expression, jitter range and distribution,
misfire/overlap policy, retry, and a live runs table. Configurations
made in Studio call directly into the SDK; the underlying
`ScheduledJob` is the same handle you'd get in code.
