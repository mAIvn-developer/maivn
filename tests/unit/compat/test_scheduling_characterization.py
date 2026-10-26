"""Characterization tests for the v1-compatible scheduling API.

Pure schedule/jitter/retry arithmetic is pinned directly. Job behavior is driven
through ``trigger_now`` where possible so it runs on the calling thread; the few
background-timer cases use intervals of tens of milliseconds and wait on events.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from maivn import (
    AtSchedule,
    CronInvocationBuilder,
    CronSchedule,
    IntervalSchedule,
    JitterSpec,
    Retry,
    RunRecord,
    Schedule,
    ScheduledJob,
    list_jobs,
    stop_all_jobs,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_FAR_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 2.0) -> bool:
    """Poll a concurrent condition without a fixed sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _job(runner: Callable[[RunRecord], object], **kwargs: object) -> ScheduledJob:
    return ScheduledJob(
        name='probe',
        schedule=AtSchedule(_FAR_FUTURE),
        runner=runner,
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


# --- schedules ---------------------------------------------------------------


def test_base_schedule_has_no_fire_times() -> None:
    """The abstract schedule refuses to compute a next time."""
    with pytest.raises(NotImplementedError):
        _ = Schedule().next_after(_EPOCH)


def test_upcoming_stops_when_the_schedule_is_exhausted() -> None:
    """A one-shot schedule yields at most one upcoming time."""
    when = _EPOCH + timedelta(hours=1)
    schedule = AtSchedule(when)
    assert schedule.upcoming(3, after=_EPOCH) == [when]
    assert schedule.upcoming(3, after=when) == []
    assert schedule.upcoming(0, after=_EPOCH) == []


def test_at_schedule_is_strictly_after_and_attaches_timezone_to_naive_times() -> None:
    """A naive ``when`` adopts the schedule timezone; equal times are not upcoming."""
    schedule = AtSchedule(datetime(2026, 1, 1, 12, 0), tz='Europe/Paris')  # noqa: DTZ001
    assert schedule.when == datetime(2026, 1, 1, 12, 0, tzinfo=ZoneInfo('Europe/Paris'))
    assert schedule.next_after(schedule.when - timedelta(microseconds=1)) == schedule.when
    assert schedule.next_after(schedule.when) is None
    utc_noon = AtSchedule(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc), tz='Europe/Paris')
    assert utc_noon.when.utcoffset() == timedelta(hours=1)
    assert utc_noon.when.hour == 13  # noqa: PLR2004 - noon UTC is 13:00 in Paris


def test_interval_schedule_rejects_non_positive_intervals() -> None:
    """Zero and negative intervals are refused."""
    for interval in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError, match=r'^IntervalSchedule.interval must be positive$'):
            _ = IntervalSchedule(interval)


def test_interval_schedule_steps_strictly_forward_from_start() -> None:
    """Before start yields start; on or after start yields the next grid point."""
    start = _EPOCH
    schedule = IntervalSchedule(timedelta(minutes=10), start=start)
    assert schedule.next_after(start - timedelta(days=1)) == start
    assert schedule.next_after(start) == start + timedelta(minutes=10)
    assert schedule.next_after(start + timedelta(minutes=9)) == start + timedelta(minutes=10)
    assert schedule.next_after(start + timedelta(minutes=10)) == start + timedelta(minutes=20)
    assert schedule.upcoming(3, after=start) == [
        start + timedelta(minutes=10),
        start + timedelta(minutes=20),
        start + timedelta(minutes=30),
    ]


def test_interval_schedule_defaults_to_now_in_utc() -> None:
    """Without a start the grid begins now, in UTC."""
    before = datetime.now(timezone.utc)
    schedule = IntervalSchedule(timedelta(hours=1))
    assert schedule.tz is timezone.utc
    assert before <= schedule.start <= datetime.now(timezone.utc)


def test_cron_schedule_uses_its_timezone() -> None:
    """Cron fields are evaluated in the schedule's timezone."""
    schedule = CronSchedule('0 9 * * *', tz='America/New_York')
    after = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
    next_at = schedule.next_after(after)
    assert next_at is not None
    assert next_at.astimezone(timezone.utc) == datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    fixed = CronSchedule('*/15 * * * *', tz=timezone.utc)
    assert fixed.next_after(datetime(2026, 1, 1, 0, 0)) == datetime(  # noqa: DTZ001
        2026, 1, 1, 0, 15, tzinfo=timezone.utc
    )


# --- jitter -------------------------------------------------------------------


def test_jitter_from_value_coerces_shorthands() -> None:
    """None and specs pass through; tuples, timedeltas and numbers become specs."""
    spec = JitterSpec(max=timedelta(seconds=1))
    assert JitterSpec.from_value(None) is None
    assert JitterSpec.from_value(spec) is spec
    from_tuple = JitterSpec.from_value((1, timedelta(seconds=3)))
    assert from_tuple is not None
    assert (from_tuple.min, from_tuple.max) == (timedelta(seconds=1), timedelta(seconds=3))
    from_float = JitterSpec.from_value(1.5)
    assert from_float is not None
    assert (from_float.min, from_float.max) == (timedelta(seconds=-1.5), timedelta(seconds=1.5))
    from_delta = JitterSpec.from_value(timedelta(seconds=2))
    assert from_delta is not None
    assert (from_delta.min, from_delta.max) == (timedelta(seconds=-2), timedelta(seconds=2))
    assert from_delta.distribution == 'uniform'


@pytest.mark.parametrize('value', ['5', (1, 2, 3), (1,)])
def test_jitter_from_value_rejects_unsupported_shorthands(value: object) -> None:
    """Strings and tuples of the wrong length are type errors."""
    with pytest.raises(TypeError, match=r'^') as excinfo:
        _ = JitterSpec.from_value(value)  # pyright: ignore[reportArgumentType]
    assert str(excinfo.value) == f'unsupported jitter shorthand: {value!r}'


def test_symmetric_jitter_keeps_distribution_and_seed() -> None:
    """``symmetric`` mirrors the amount around zero."""
    spec = JitterSpec.symmetric(2, distribution='triangular', seed=7)
    assert (spec.min, spec.max, spec.distribution, spec.seed) == (
        timedelta(seconds=-2),
        timedelta(seconds=2),
        'triangular',
        7,
    )


def test_degenerate_jitter_returns_its_fixed_offset() -> None:
    """When min equals max the offset is exactly that value."""
    spec = JitterSpec(min=timedelta(seconds=3), max=timedelta(seconds=3), distribution='normal')
    assert {spec.sample() for _ in range(5)} == {timedelta(seconds=3)}


@pytest.mark.parametrize('distribution', ['uniform', 'normal', 'triangular'])
def test_seeded_jitter_is_reproducible_and_bounded(distribution: str) -> None:
    """Equal seeds give equal sequences inside [min, max]."""
    low, high = timedelta(seconds=-4), timedelta(seconds=6)
    first = JitterSpec(min=low, max=high, distribution=distribution, seed=11)  # pyright: ignore[reportArgumentType]
    second = JitterSpec(min=low, max=high, distribution=distribution, seed=11)  # pyright: ignore[reportArgumentType]
    samples = [first.sample() for _ in range(200)]
    assert samples == [second.sample() for _ in range(200)]
    assert all(low <= sample <= high for sample in samples)
    assert len(set(samples)) > 1


def test_normal_jitter_clamps_wide_samples_to_the_bounds() -> None:
    """A huge sigma is clamped, so samples land exactly on the bounds."""
    spec = JitterSpec(
        min=timedelta(seconds=-1),
        max=timedelta(seconds=1),
        distribution='normal',
        sigma=timedelta(seconds=1000),
        seed=3,
    )
    samples = {spec.sample() for _ in range(50)}
    assert samples <= {timedelta(seconds=-1), timedelta(seconds=1)}
    assert samples == {timedelta(seconds=-1), timedelta(seconds=1)}


def test_normal_jitter_default_sigma_is_a_sixth_of_the_range() -> None:
    """Without sigma, the normal draw uses (high - low) / 6 around the midpoint."""
    spec = JitterSpec(
        min=timedelta(seconds=0),
        max=timedelta(seconds=600),
        distribution='normal',
        seed=5,
    )
    samples = [spec.sample().total_seconds() for _ in range(2000)]
    mean = sum(samples) / len(samples)
    spread = (sum((s - mean) ** 2 for s in samples) / len(samples)) ** 0.5
    assert 290 < mean < 310  # noqa: PLR2004 - midpoint 300
    assert 90 < spread < 110  # noqa: PLR2004 - sigma 100


def test_triangular_and_uniform_draws_differ_in_shape() -> None:
    """Triangular draws concentrate near the middle; uniform draws do not."""
    low, high = timedelta(seconds=0), timedelta(seconds=100)
    tri = JitterSpec(min=low, max=high, distribution='triangular', seed=1)
    uni = JitterSpec(min=low, max=high, distribution='uniform', seed=1)
    tri_edges = sum(1 for _ in range(2000) if not 20 <= tri.sample().total_seconds() <= 80)  # noqa: PLR2004
    uni_edges = sum(1 for _ in range(2000) if not 20 <= uni.sample().total_seconds() <= 80)  # noqa: PLR2004
    assert tri_edges < 450  # noqa: PLR2004 - triangular expects 16%
    assert uni_edges > 650  # noqa: PLR2004 - uniform expects 40%


def test_jitter_apply_reports_overrun_of_the_next_fire() -> None:
    """Fires landing on or after the next scheduled time are skipped unless allowed."""
    offset = timedelta(seconds=10)
    spec = JitterSpec(min=offset, max=offset)
    scheduled = _EPOCH
    assert spec.apply(scheduled, scheduled + offset) == (scheduled + offset, offset, True)
    assert spec.apply(scheduled, scheduled + timedelta(seconds=11)) == (
        scheduled + offset,
        offset,
        False,
    )
    assert spec.apply(scheduled) == (scheduled + offset, offset, False)
    lenient = JitterSpec(min=offset, max=offset, skip_if_overruns_next=False)
    assert lenient.apply(scheduled, scheduled) == (scheduled + offset, offset, False)


# --- retry --------------------------------------------------------------------


def test_retry_defaults() -> None:
    """The default policy makes one attempt with constant 5s spacing."""
    retry = Retry()
    assert retry.max_attempts == 1
    assert retry.delay_for_attempt(2) == timedelta(seconds=5)
    assert retry.should_retry(RuntimeError('x'), 1) is False


@pytest.mark.parametrize(
    ('backoff', 'attempt', 'expected'),
    [
        ('constant', 0, 0),
        ('constant', 1, 0),
        ('constant', 2, 5),
        ('constant', 5, 5),
        ('linear', 2, 5),
        ('linear', 4, 15),
        ('exponential', 2, 5),
        ('exponential', 3, 15),
        ('exponential', 4, 45),
    ],
)
def test_retry_delay_by_backoff(backoff: str, attempt: int, expected: int) -> None:
    """Delays grow per the backoff mode from the first retry onward."""
    retry = Retry(backoff=backoff, base=timedelta(seconds=5), factor=3.0)  # pyright: ignore[reportArgumentType]
    assert retry.delay_for_attempt(attempt) == timedelta(seconds=expected)


def test_retry_delay_is_capped_unless_uncapped() -> None:
    """``max_delay`` caps the delay; None removes the cap; equal is not capped."""
    capped = Retry(backoff='linear', base=timedelta(seconds=10), max_delay=timedelta(seconds=25))
    assert capped.delay_for_attempt(3) == timedelta(seconds=20)
    assert capped.delay_for_attempt(4) == timedelta(seconds=25)
    assert capped.delay_for_attempt(9) == timedelta(seconds=25)
    uncapped = Retry(backoff='exponential', base=timedelta(minutes=10), max_delay=None)
    assert uncapped.delay_for_attempt(4) == timedelta(minutes=40)
    exact = Retry(base=timedelta(seconds=30), max_delay=timedelta(seconds=30))
    assert exact.delay_for_attempt(2) == timedelta(seconds=30)


def test_retry_should_retry_respects_attempts_and_exception_types() -> None:
    """Retries stop at max_attempts and only for configured exception types."""
    retry = Retry(max_attempts=3, retry_on=(ValueError,))
    assert retry.should_retry(ValueError('x'), 1) is True
    assert retry.should_retry(ValueError('x'), 2) is True
    assert retry.should_retry(ValueError('x'), 3) is False
    assert retry.should_retry(KeyError('x'), 1) is False


def test_run_record_duration() -> None:
    """Duration exists only when both timestamps are set."""
    record = RunRecord(scheduled_at=_EPOCH, fire_id='f')
    assert record.duration is None
    record.fired_at = _EPOCH
    assert record.duration is None
    record.finished_at = _EPOCH + timedelta(seconds=2)
    assert record.duration == timedelta(seconds=2)
    assert (record.status, record.attempt, record.metadata) == ('pending', 1, {})


# --- jobs (manual triggers) ------------------------------------------------------


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'overlap_policy': 'merge'}, "unsupported overlap policy: 'merge'"),
        ({'misfire': 'later'}, "unsupported misfire policy: 'later'"),
    ],
)
def test_scheduled_job_rejects_unknown_policies(kwargs: dict[str, str], message: str) -> None:
    """Unknown overlap and misfire policies are refused at construction."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = _job(lambda _record: None, **kwargs)
    assert str(excinfo.value) == message


def test_trigger_now_runs_synchronously_and_records_history() -> None:
    """A manual trigger runs the callable now and records a succeeded run."""
    seen: list[str] = []
    job = _job(lambda record: f'ran-{record.attempt}')
    _ = job.on_fire(lambda record: seen.append(f'fire:{record.status}'))
    _ = job.on_success(lambda record: seen.append(f'success:{record.result}'))
    record = job.trigger_now()
    assert record.status == 'succeeded'
    assert record.result == 'ran-1'
    assert record.metadata == {
        'manual': True,
        'cancellation_event': record.metadata['cancellation_event'],
    }
    assert isinstance(record.metadata['cancellation_event'], threading.Event)
    assert record.fired_at is not None
    assert record.finished_at is not None
    assert record.finished_at >= record.fired_at
    assert seen == ['fire:running', 'success:ran-1']
    assert (job.fire_count, job.success_count, job.failure_count, job.skip_count) == (1, 1, 0, 0)
    assert job.history() == [record]
    assert job.last_run is record
    assert job.is_running is False
    assert job.is_done is False


def test_trigger_now_awaits_async_runners_and_callbacks() -> None:
    """Awaitable results and awaitable callbacks are driven to completion."""
    observed: list[object] = []

    async def runner(_record: RunRecord) -> str:
        await asyncio.sleep(0)
        return 'async-result'

    async def on_success(record: RunRecord) -> None:
        await asyncio.sleep(0)
        observed.append(record.result)

    job = _job(runner).on_success(on_success)
    record = job.trigger_now()
    assert record.result == 'async-result'
    assert observed == ['async-result']


def test_async_callbacks_inside_a_running_loop_are_scheduled_as_tasks() -> None:
    """With a running loop, awaitable callbacks become tasks and their errors are swallowed."""
    observed: list[str] = []

    async def failing(_record: RunRecord) -> None:
        await asyncio.sleep(0)
        message = 'callback failure'
        raise RuntimeError(message)

    async def recording(_record: RunRecord) -> None:
        await asyncio.sleep(0)
        observed.append('task-ran')

    async def main() -> tuple[str, list[str]]:
        job = _job(lambda _record: 'ok').on_success(failing).on_success(recording)
        record = job.trigger_now()
        before = list(observed)
        for _ in range(5):
            await asyncio.sleep(0)
        return record.status, before

    status, before = asyncio.run(main())
    assert status == 'succeeded'
    assert before == []
    assert observed == ['task-ran']


def test_callback_errors_do_not_stop_later_callbacks_or_the_run() -> None:
    """A raising observer is suppressed and the next observer still runs."""
    seen: list[str] = []

    def broken(_record: RunRecord) -> None:
        message = 'observer failure'
        raise RuntimeError(message)

    job = _job(lambda _record: 1).on_success(broken).on_success(lambda _r: seen.append('next'))
    assert job.trigger_now().status == 'succeeded'
    assert seen == ['next']


def test_failed_runs_retry_then_report_failure() -> None:
    """Retries consume attempts; the final error is recorded and observers notified."""
    attempts: list[int] = []
    errors: list[BaseException | None] = []

    def runner(record: RunRecord) -> None:
        attempts.append(record.attempt)
        message = f'attempt {record.attempt}'
        raise ValueError(message)

    job = _job(runner, retry=Retry(max_attempts=3, base=timedelta(0)))
    _ = job.on_error(lambda record: errors.append(record.error))
    record = job.trigger_now()
    assert attempts == [1, 2, 3]
    assert record.status == 'failed'
    assert record.attempt == 3  # noqa: PLR2004
    assert isinstance(record.error, ValueError)
    assert str(record.error) == 'attempt 3'
    assert errors == [record.error]
    assert (job.success_count, job.failure_count) == (0, 1)


def test_non_retryable_errors_fail_on_the_first_attempt() -> None:
    """Exceptions outside ``retry_on`` are not retried."""
    attempts: list[int] = []

    def runner(record: RunRecord) -> None:
        attempts.append(record.attempt)
        raise KeyError(record.attempt)

    job = _job(runner, retry=Retry(max_attempts=5, base=timedelta(0), retry_on=(ValueError,)))
    assert job.trigger_now().status == 'failed'
    assert attempts == [1]


def test_stop_interrupts_a_pending_retry_wait() -> None:
    """A retry backoff wait ends as cancelled once the job is stopped."""
    attempts: list[int] = []

    def runner(record: RunRecord) -> None:
        attempts.append(record.attempt)
        message = 'transient'
        raise ValueError(message)

    job = _job(runner, retry=Retry(max_attempts=3, base=timedelta(hours=1)))
    job.stop()
    assert job.is_done is True
    started = time.monotonic()
    record = job.trigger_now()
    assert time.monotonic() - started < 1
    assert attempts == [1]
    assert record.status == 'cancelled'
    assert isinstance(record.error, ValueError)
    assert (job.success_count, job.failure_count, job.skip_count) == (0, 0, 0)
    assert job.history() == [record]


def test_history_limit_and_trimming() -> None:
    """``history(limit)`` returns the newest records; history keeps at most 1000."""
    job = _job(lambda _record: None)
    records = [job.trigger_now() for _ in range(1001)]
    history = job.history()
    assert len(history) == 1000  # noqa: PLR2004
    assert history[0] is records[1]
    assert history[-1] is records[-1]
    assert job.history(limit=2) == records[-2:]
    assert job.fire_count == 1001  # noqa: PLR2004


def test_max_runs_marks_the_job_done_after_manual_runs() -> None:
    """Reaching max_runs with every run finished marks the job done."""
    job = _job(lambda _record: None, max_runs=2)
    _ = job.trigger_now()
    assert job.is_done is False
    _ = job.trigger_now()
    assert job.is_done is True
    assert job.is_running is False


def test_pause_resume_and_next_runs() -> None:
    """Pause flags toggle; ``next_runs`` caches the first upcoming time."""
    job = ScheduledJob(
        name='grid',
        schedule=IntervalSchedule(timedelta(days=1), start=_FAR_FUTURE),
        runner=lambda _record: None,
    )
    assert job.next_run_at is None
    job.pause()
    assert job.is_paused is True
    job.resume()
    assert job.is_paused is False
    assert job.next_runs(2) == [_FAR_FUTURE, _FAR_FUTURE + timedelta(days=1)]
    assert job.next_run_at == _FAR_FUTURE
    exhausted = _job(lambda _record: None)
    exhausted.schedule = AtSchedule(_EPOCH)
    assert exhausted.next_runs() == []
    assert exhausted.next_run_at is None


def test_drain_timeout_returns_while_a_run_is_still_active() -> None:
    """``stop(timeout=...)`` gives up waiting for an active run after the timeout."""
    entered = threading.Event()
    release = threading.Event()

    def runner(_record: RunRecord) -> str:
        entered.set()
        assert release.wait(timeout=5)
        return 'late'

    job = _job(runner)
    results: list[RunRecord] = []
    worker = threading.Thread(target=lambda: results.append(job.trigger_now()))
    worker.start()
    try:
        assert entered.wait(timeout=2)
        started = time.monotonic()
        job.stop(drain=True, timeout=0.05)
        elapsed = time.monotonic() - started
        assert 0.04 <= elapsed < 1  # noqa: PLR2004
        assert job.is_done is True
        assert results == []
    finally:
        release.set()
        worker.join(timeout=5)
    assert [record.status for record in results] == ['succeeded']


def test_replace_cancels_the_queued_run_in_favour_of_the_newest() -> None:
    """A newer replace-policy fire cancels the queued one and requests active cancellation."""
    first_entered = threading.Event()
    release = threading.Event()
    ran: list[str] = []
    active: list[RunRecord] = []

    def runner(record: RunRecord) -> None:
        ran.append(record.fire_id)
        active.append(record)
        if len(ran) == 1:
            first_entered.set()
            assert release.wait(timeout=5)

    job = _job(runner, overlap_policy='replace', max_overlap=1)
    results: dict[str, RunRecord] = {}

    def trigger(label: str) -> None:
        results[label] = job.trigger_now()

    threads = {label: threading.Thread(target=trigger, args=(label,)) for label in 'ABC'}
    threads['A'].start()
    assert first_entered.wait(timeout=2)
    threads['B'].start()
    # B flags A for cancellation and joins the queue in one locked step, so once
    # the flag is visible C cannot overtake B.
    assert _wait_for(lambda: active[0].metadata.get('cancellation_requested') is True)
    threads['C'].start()
    assert _wait_for(lambda: 'B' in results)
    release.set()
    for thread in threads.values():
        thread.join(timeout=5)

    first, queued, newest = results['A'], results['B'], results['C']
    assert queued.status == 'cancelled'
    assert queued.metadata['replaced_by'] == newest.fire_id
    assert queued.metadata['replaces_active_run'] is True
    assert newest.metadata['replaces_active_run'] is True
    assert first.metadata['cancellation_requested'] is True
    event = first.metadata['cancellation_event']
    assert isinstance(event, threading.Event)
    assert event.is_set()
    assert newest.status == 'succeeded'
    assert ran == [first.fire_id, newest.fire_id]


# --- jobs (background timer) ------------------------------------------------------


def test_jitter_that_overruns_the_next_tick_skips_the_fire() -> None:
    """A scheduled fire whose jitter reaches the next tick is skipped and observed."""
    interval = timedelta(milliseconds=40)
    skipped = threading.Event()
    job = ScheduledJob(
        name='jittery',
        schedule=IntervalSchedule(interval),
        runner=lambda _record: pytest.fail('a skipped fire must not run'),
        jitter=JitterSpec(min=interval, max=interval),
        max_runs=1,
    )
    _ = job.on_skip(lambda _record: skipped.set())
    try:
        _ = job.start()
        assert skipped.wait(timeout=2)
        assert _wait_for(lambda: job.is_done)
    finally:
        job.stop(timeout=2)
    (record,) = job.history()
    assert record.status == 'skipped_jitter'
    assert record.jitter_offset == interval
    assert record.fired_at is None
    assert (job.fire_count, job.skip_count, job.success_count) == (1, 1, 0)


def test_stop_cancels_a_fire_waiting_out_its_jitter() -> None:
    """Stopping during a jitter delay cancels that fire without running it."""
    job = ScheduledJob(
        name='delayed',
        schedule=IntervalSchedule(timedelta(milliseconds=20)),
        runner=lambda _record: pytest.fail('a cancelled fire must not run'),
        jitter=JitterSpec(
            min=timedelta(hours=1),
            max=timedelta(hours=1),
            skip_if_overruns_next=False,
        ),
        max_runs=1,
    )
    try:
        _ = job.start()
        assert _wait_for(lambda: job.fire_count == 1)
    finally:
        job.stop(drain=True, timeout=2)
    assert _wait_for(lambda: len(job.history()) == 1)
    (record,) = job.history()
    assert record.status == 'cancelled'
    assert record.jitter_offset == timedelta(hours=1)
    assert (job.success_count, job.failure_count, job.skip_count) == (0, 0, 0)


def test_a_paused_job_does_not_fire_until_resumed() -> None:
    """Pausing before start holds fires back until resume."""
    fired = threading.Event()
    job = ScheduledJob(
        name='paused',
        schedule=IntervalSchedule(timedelta(milliseconds=10)),
        runner=lambda _record: fired.set(),
        max_runs=1,
    )
    job.pause()
    try:
        _ = job.start()
        assert job.is_running is True
        assert fired.wait(timeout=0.1) is False
        assert job.fire_count == 0
        job.resume()
        assert fired.wait(timeout=2)
    finally:
        job.stop(timeout=2)


def test_start_is_idempotent_and_registers_the_job() -> None:
    """Starting twice is harmless; started jobs are listed; stop_all_jobs stops them."""
    job = _job(lambda _record: None)
    assert job.start() is job
    assert job.start() is job
    assert job in list_jobs()
    assert job.is_running is True
    stop_all_jobs(timeout=2)
    assert job.is_running is False
    assert job.is_done is True
    assert job.start() is job
    assert job.is_running is False


def test_stopped_and_finished_jobs_leave_the_live_list() -> None:
    """``list_jobs()`` returns live jobs only (U05-D7)."""
    stopped = _job(lambda _record: None).start()
    finished = _job(lambda _record: None, max_runs=1).start()
    live = _job(lambda _record: None).start()
    try:
        stopped.stop(timeout=2)
        _ = finished.trigger_now()
        assert _wait_for(lambda: finished.is_done)

        jobs = list_jobs()
        assert stopped not in jobs
        assert finished not in jobs
        assert live in jobs
    finally:
        for job in (stopped, finished, live):
            job.stop(timeout=2)


def test_start_window_defers_the_first_fire_to_the_window_start() -> None:
    """The first fire is the first grid time at or after ``start_at``."""
    start = datetime.now(timezone.utc)
    interval = timedelta(milliseconds=25)
    start_at = start + 4 * interval
    done = threading.Event()
    job = ScheduledJob(
        name='windowed',
        schedule=IntervalSchedule(interval, start=start),
        runner=lambda _record: done.set(),
        start_at=start_at,
        max_runs=1,
    )
    try:
        _ = job.start()
        assert done.wait(timeout=2)
    finally:
        job.stop(timeout=2)
    (record,) = job.history()
    assert record.scheduled_at == start_at


def test_end_window_before_the_next_fire_finishes_the_job() -> None:
    """A schedule whose next time is past ``end_at`` ends without firing."""
    job = ScheduledJob(
        name='ended',
        schedule=AtSchedule(_FAR_FUTURE),
        runner=lambda _record: pytest.fail('must not run'),
        end_at=_EPOCH,
    )
    _ = job.start()
    assert _wait_for(lambda: job.is_done)
    job.stop(timeout=2)
    assert job.next_run_at is None
    assert job.fire_count == 0


# --- builder ----------------------------------------------------------------------


class _Scope:
    """Scope double exposing every schedulable method."""

    not_callable = 'value'

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def invoke(self, *args: object, **kwargs: object) -> str:
        self.calls.append(('invoke', args, kwargs))
        return 'invoked'

    def batch(self, inputs: list[object], **kwargs: object) -> list[object]:
        self.calls.append(('batch', (inputs,), kwargs))
        return inputs

    async def abatch(self, inputs: list[object], **kwargs: object) -> list[object]:
        self.calls.append(('abatch', (inputs,), kwargs))
        return [*inputs, 'async']

    async def ainvoke(self, *args: object, **kwargs: object) -> str:
        self.calls.append(('ainvoke', args, kwargs))
        return 'ainvoked'

    def stream(self, *args: object, **kwargs: object) -> object:
        self.calls.append(('stream', args, kwargs))
        return iter(['a', 'b'])

    async def _agen(self) -> AsyncIterator[str]:
        yield 'x'
        yield 'y'

    def astream(self, *args: object, **kwargs: object) -> AsyncIterator[str]:
        self.calls.append(('astream', args, kwargs))
        return self._agen()


def test_builder_methods_schedule_the_matching_scope_call() -> None:
    """Each builder verb schedules the same-named scope method with its arguments."""
    scope = _Scope()
    builder = CronInvocationBuilder(scope, AtSchedule(_FAR_FUTURE))
    jobs = [
        builder.invoke('hello', mode='x'),
        builder.batch(iter([1, 2]), max_concurrency=2),
        builder.abatch((3,)),
        builder.ainvoke('async'),
        builder.stream('s'),
        builder.astream(),
    ]
    try:
        results = [job.trigger_now().result for job in jobs]
    finally:
        for job in jobs:
            job.stop(timeout=2)
    assert results == ['invoked', [1, 2], [3, 'async'], 'ainvoked', ['a', 'b'], ['x', 'y']]
    assert scope.calls == [
        ('invoke', ('hello',), {'mode': 'x'}),
        ('batch', ([1, 2],), {'max_concurrency': 2}),
        ('abatch', ([3],), {}),
        ('ainvoke', ('async',), {}),
        ('stream', ('s',), {}),
        ('astream', (), {}),
    ]
    assert {job.name for job in jobs} == {'_Scope'}


def test_builder_setters_configure_the_scheduled_job() -> None:
    """Fluent setters carry name, window, max runs, events, scope and retry to the job."""
    first, second = _Scope(), _Scope()
    start_at = _EPOCH
    end_at = _FAR_FUTURE
    attempts: list[int] = []

    class Flaky(_Scope):
        def invoke(self, *args: object, **kwargs: object) -> str:
            attempts.append(len(attempts) + 1)
            if len(attempts) < 2:  # noqa: PLR2004
                message = 'flaky'
                raise ValueError(message)
            return super().invoke(*args, **kwargs)

    flaky = Flaky()
    builder = (
        CronInvocationBuilder(first, AtSchedule(_FAR_FUTURE), name='nightly')
        .with_scope(second)
        .with_scope(flaky)
        .with_jitter(None)
        .with_retry(Retry(max_attempts=2, base=timedelta(0)))
        .with_overlap('queue', max_overlap=2)
        .with_misfire('skip')
        .with_window(start_at=start_at, end_at=end_at)
        .with_window()
        .with_max_runs(3)
        .with_emit_events()
    )
    job = builder.invoke()
    try:
        record = job.trigger_now()
    finally:
        job.stop(timeout=2)
    assert (job.name, job.start_at, job.end_at, job.max_runs, job.emit_events) == (
        'nightly',
        start_at,
        end_at,
        3,
        True,
    )
    assert record.status == 'succeeded'
    assert attempts == [1, 2]
    assert first.calls == []
    assert second.calls == []
    assert builder.with_emit_events(False).invoke().emit_events is False  # noqa: FBT003
    stop_all_jobs(timeout=2)


def test_builder_with_jitter_shorthand_skips_overrunning_fires() -> None:
    """``with_jitter`` accepts shorthand values and applies them to scheduled fires."""
    interval = timedelta(milliseconds=40)
    scope = _Scope()
    job = (
        CronInvocationBuilder(scope, IntervalSchedule(interval))
        .with_jitter((interval, interval))  # pyright: ignore[reportArgumentType]
        .with_max_runs(1)
        .invoke()
    )
    try:
        assert _wait_for(lambda: job.is_done)
    finally:
        job.stop(timeout=2)
    assert [record.status for record in job.history()] == ['skipped_jitter']
    assert scope.calls == []


def test_builder_refuses_a_non_callable_scope_attribute() -> None:
    """A scope attribute that is not callable fails the run with a type error."""

    class Scope:
        invoke = 'not a method'

    job = CronInvocationBuilder(Scope(), AtSchedule(_FAR_FUTURE)).invoke()
    try:
        record = job.trigger_now()
    finally:
        job.stop(timeout=2)
    assert record.status == 'failed'
    assert isinstance(record.error, TypeError)
    assert str(record.error) == 'scope method is not callable: invoke'
