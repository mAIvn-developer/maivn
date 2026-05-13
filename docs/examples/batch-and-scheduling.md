# Batch & Scheduling

Run multiple inputs concurrently, or run agents on a schedule. The same
chain works for `Agent` and `Swarm`.

## Batch invocation

`agent.batch([msgs_1, msgs_2, ...])` runs multiple independent invocations
concurrently and returns responses in input order:

```python
from maivn import Agent
from maivn.messages import HumanMessage

incident_summarizer = Agent(
    name='Incident Summarizer',
    system_prompt='Summarize one support incident at a time using emit_incident_summary.',
    api_key='...',
)

# ... register tools ...

inputs = [
    [HumanMessage(content='Summarize incident BATCH-1001. P1 API outage.')],
    [HumanMessage(content='Summarize incident BATCH-2002. P2 duplicate invoices.')],
    [HumanMessage(content='Summarize incident BATCH-3003. P3 dashboard empty state.')],
]

responses = incident_summarizer.batch(
    inputs,
    max_concurrency=3,
    force_final_tool=True,
)

for i, response in enumerate(responses, start=1):
    print(f'#{i}: {response.result}')
```

Each list element is a normal `invoke(...)` payload; the runtime schedules
them with the concurrency cap you set.

## Async batch

```python
import asyncio

async def run():
    responses = await incident_summarizer.abatch(
        inputs,
        max_concurrency=3,
        force_final_tool=True,
    )
    return responses

asyncio.run(run())
```

`abatch` is preferred when you're already inside an event loop. Swarms have
the same methods (`swarm.batch`, `swarm.abatch`).

## Scheduling: cron

`agent.cron(...)` returns a `ScheduledJob` that fires on a cron schedule.
Keep a reference to the job for as long as you want it to run:

```python
from datetime import timedelta
from maivn import Agent, JitterSpec, Retry
from maivn.messages import HumanMessage

briefer = Agent(
    name='Daily Briefing',
    system_prompt='Compose the daily ops briefing in two sentences.',
    api_key='...',
)

job = briefer.cron(
    '0 9 * * MON-FRI',                              # weekdays at 09:00
    tz='America/New_York',
    jitter=JitterSpec.symmetric(timedelta(minutes=10)),
    retry=Retry(max_attempts=3, backoff='exponential', base=timedelta(seconds=30)),
).invoke([HumanMessage(content='Compose the daily ops briefing.')])

job.on_fire(lambda r: print(f'fired at {r.fired_at}, jitter={r.jitter_offset}'))
job.on_error(lambda r: alert(r.error))
```

The same chain works for `.stream(...)`, `.batch(...)`, and the async
variants. See the [Scheduled Invocation guide](../guides/scheduled-invocation.md)
for the cron expression cheat sheet, jitter strategies, and overlap policies.

## Scheduling: fixed interval

```python
from datetime import timedelta

job = agent.every(timedelta(minutes=5)).invoke(messages)
```

Use `every` (not cron `*/5 * * * *`) when you want a true 5-minute spacing
relative to registration, instead of "every minute divisible by 5."

## Scheduling: one-shot

```python
from datetime import datetime, timedelta, timezone

when = datetime.now(timezone.utc) + timedelta(days=14)
job = agent.at(when).invoke(messages)
```

## Scheduled batch with retry/backoff

Combining scheduling with batch is the same chain — `scope.cron(...).batch(...)`:

```python
inbox_batch = [
    [HumanMessage(content='Customer asks why their last invoice was higher than usual.')],
    [HumanMessage(content='Customer cannot reset their password.')],
    [HumanMessage(content='Prospect wants a quote for 5 enterprise seats.')],
]

job = inbox_agent.cron(
    '*/10 * * * *',
    tz='UTC',
    jitter=JitterSpec(min=timedelta(0), max=timedelta(seconds=20)),
    retry=Retry(
        max_attempts=4,
        backoff='exponential',
        base=timedelta(seconds=2),
        max_delay=timedelta(seconds=30),
    ),
).batch(inbox_batch, max_concurrency=3)

job.on_success(lambda r: print(f'[OK] {r.fire_id} attempt={r.attempt}'))
job.on_error(lambda r: print(f'[ERR] {r.fire_id} attempts_used={r.attempt}'))
```

Retries happen *within a single fire*, so a flaky run doesn't burn the
next scheduled slot. Only the final outcome is recorded on the
`RunRecord`.

## Asymmetric jitter for production schedules

Perfect periodicity makes cron jobs easy to fingerprint and gives
downstream systems coordinated load spikes. Asymmetric jitter (always
positive) produces a more natural fire pattern:

```python
from maivn import JitterSpec

asymmetric = JitterSpec(
    min=timedelta(seconds=0),
    max=timedelta(seconds=120),
    distribution='triangular',     # most fires near the scheduled time
    align_to=timedelta(seconds=5), # snap to a 5-second grid
)

job = briefing_swarm.cron(
    '*/5 * * * *',
    tz='UTC',
    jitter=asymmetric,
    overlap_policy='skip',         # don't pile up if a run is slow
    max_overlap=1,
).invoke([HumanMessage(content='Compose the daily ops briefing.')])
```

`distribution='triangular'` makes fires cluster near the scheduled time
with a tail outward. Other options: `'uniform'` (flat distribution) and
`'normal'` (Gaussian around the center, configurable `sigma`).

## Overlap policies

Configure what happens if a new fire arrives before the previous one
finishes:

| Policy | Behavior |
| --- | --- |
| `skip` (default) | Drop the new fire (status = `skipped_overlap`). |
| `queue` | Wait for a free slot. |
| `replace` | Run anyway; tag the new run with `replaced_token`. |

```python
job = agent.cron('*/2 * * * *', overlap_policy='skip', max_overlap=1).invoke(messages)
```

For most production workloads, `skip` + `max_overlap=1` is the safe
default — agents that hold non-idempotent state should never overlap.

## Inspection and lifecycle

```python
job.next_run_at                  # datetime | None
job.next_runs(5)                 # next 5 scheduled times
job.fire_count                   # successful + failed + skipped
job.last_run                     # most recent RunRecord
for record in job.history():
    print(record.status, record.attempt, record.duration)

job.pause()
job.resume()
job.trigger_now()                # fire once outside the schedule
job.stop(drain=True, timeout=30) # graceful shutdown
```

For async event-stream inspection:

```python
async for record in job.events():
    log.info('fire %s -> %s', record.fire_id, record.status)
```

## Live activity in Studio

mAIvn Studio surfaces the runs table for every scheduled app and streams
updates as fires happen — no polling delay between the countdown hitting
zero and the run card appearing. Status pills flip from running to
succeeded / failed / skipped as soon as the matching terminal callback
fires server-side.

## Production checklist

- **Pin the timezone** with `tz=...` — never rely on the host's local clock.
- **Use jitter** for any fan-out larger than a handful of jobs.
- **Set `max_runs` or `end_at`** for staged rollouts so the job has a natural
  end.
- **Wire `on_error`** to your alerting; a fire that exhausted retries does
  not raise back into your code.
- **Stop on shutdown**: call `job.stop()` (or `stop_all_jobs()`) from your
  process shutdown hook so in-flight runs drain.
- **Cap concurrency**: leave `max_overlap=1` unless you've proven the
  workload is safe to interleave.

## What's next

- **[Scheduled Invocation guide](../guides/scheduled-invocation.md)** — the
  deep dive on cron syntax, time zones, DST handling, jitter shapes, misfire
  policies, and the full lifecycle API.
- **[Interrupts](./interrupts.md)** — for jobs that need human input
  mid-execution.
